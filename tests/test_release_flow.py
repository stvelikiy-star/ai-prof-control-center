import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.release_flow import prepare


class ReleaseFlowTests(unittest.TestCase):
    def make_root(self, *, profile):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        project = root / "project"
        project.mkdir()

        subprocess.run(
            ["git", "init", "-b", "release"],
            cwd=project,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "config", "user.name", "AI PROF Test"],
            cwd=project,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=project,
            check=True,
        )

        (project / "README.md").write_text("test\n", encoding="utf-8")

        subprocess.run(
            ["git", "add", "README.md"],
            cwd=project,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "test"],
            cwd=project,
            check=True,
            stdout=subprocess.DEVNULL,
        )

        orchestrator = root / "orchestrator"
        orchestrator.mkdir()

        entry = {
            "project_id": "test-project",
            "path": str(project),
            "base_branch": "develop",
        }

        if profile is not None:
            entry["release"] = profile

        (orchestrator / "projects.json").write_text(
            json.dumps({"projects": [entry]}),
            encoding="utf-8",
        )

        return temp, root, project


    def test_missing_profile_blocks_release(self):
        temp, root, _project = self.make_root(profile=None)
        self.addCleanup(temp.cleanup)

        report = prepare(root, "test-project", environ={})

        self.assertEqual(report["state"], "OWNER_ACTION_REQUIRED")
        self.assertEqual(report["blockers"], ["RELEASE_PROFILE_MISSING"])
        self.assertFalse(report["production_changed"])


    def test_ready_profile_passes(self):
        temp, root, project = self.make_root(profile={})
        self.addCleanup(temp.cleanup)

        backup = root / "backup.sh"
        backup.write_text(
            "#!/bin/sh\n# ak-bermet supabase pg_dump\n",
            encoding="utf-8",
        )

        secret_file = root / "release.env"
        secret_file.write_text("REQUIRED_SECRET=value\n", encoding="utf-8")

        registry_path = root / "orchestrator/projects.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["projects"][0]["release"] = {
            "branch": "release",
            "required_commands": ["git", "python3"],
            "required_environment": ["REQUIRED_SECRET"],
            "secret_file": str(secret_file),
            "backup_script": str(backup),
            "backup_markers": ["ak-bermet", "supabase", "pg_dump"],
            "checks": [
                ["python3", "-c", "print('PASS')"],
            ],
        }
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        report = prepare(root, "test-project", environ={})

        self.assertEqual(report["state"], "RELEASE_READY")
        self.assertEqual(report["blockers"], [])
        self.assertTrue(
            all(check["status"] == "PASS" for check in report["checks"])
        )


    def test_missing_secret_and_backup_marker_are_reported(self):
        temp, root, _project = self.make_root(profile={})
        self.addCleanup(temp.cleanup)

        backup = root / "backup.sh"
        backup.write_text("#!/bin/sh\n# generic backup\n", encoding="utf-8")

        registry_path = root / "orchestrator/projects.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["projects"][0]["release"] = {
            "branch": "release",
            "required_commands": ["git"],
            "required_environment": ["MISSING_SECRET"],
            "backup_script": str(backup),
            "backup_markers": ["supabase"],
            "checks": [],
        }
        registry_path.write_text(json.dumps(registry), encoding="utf-8")

        report = prepare(root, "test-project", environ={})

        self.assertEqual(report["state"], "OWNER_ACTION_REQUIRED")
        self.assertIn(
            "MISSING_ENVIRONMENT:MISSING_SECRET",
            report["blockers"],
        )
        self.assertIn(
            "BACKUP_MARKER_MISSING:supabase",
            report["blockers"],
        )


if __name__ == "__main__":
    unittest.main()
