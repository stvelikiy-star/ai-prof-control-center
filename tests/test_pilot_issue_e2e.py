from __future__ import annotations
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"orchestrator"))
import pilot_issue_e2e as p


def valid_issue():
    return {
        "number": 1, "state": "open",
        "user": {"login": "stvelikiy-star"},
        "title": p.EXPECTED_TITLE,
        "body": p.MARKER+"\nScope-Files: scripts/task_health.py, tests/test_task_health.py\nBase-Branch: main\n",
    }


class PilotE2ETests(unittest.TestCase):
    def test_authorized_issue_contract(self):
        self.assertTrue(p.validate_issue(valid_issue()).startswith(p.MARKER))

    def test_other_author_is_rejected(self):
        x = valid_issue()
        x["user"]["login"] = "other"
        with self.assertRaises(p.PilotError):
            p.validate_issue(x)

    def test_changed_files_parser_preserves_porcelain_leading_space(self):
        with patch.object(p, "git", return_value=" M scripts/task_health.py\n M tests/test_task_health.py"):
            self.assertEqual(p.changed_files(), p.SCOPE)

    def test_scope_widening_is_rejected(self):
        with patch.object(p, "git", return_value=" M scripts/task_health.py\n?? README.md"):
            with self.assertRaisesRegex(p.PilotError, "outside fixed scope"):
                p.changed_files()

    def test_invalid_base_and_scope_rejected(self):
        for fragment in ("Base-Branch: staging", "Scope-Files: scripts/x.py"):
            x=valid_issue()
            x["body"]=x["body"].replace("Base-Branch: main",fragment)
            if fragment.startswith("Scope"):
                x["body"]=x["body"].replace("Scope-Files: scripts/task_health.py, tests/test_task_health.py",fragment)
            with self.assertRaises(p.PilotError):
                p.validate_issue(x)

    def test_no_duplication_branch_and_fixed_repo(self):
        self.assertEqual(p.REPO, "stvelikiy-star/ai-prof-autonomy-pilot")
        self.assertEqual(p.BRANCH, "feature/ai-prof-pilot-issue-1")
        self.assertEqual(p.SCOPE, frozenset({"scripts/task_health.py", "tests/test_task_health.py"}))


if __name__ == "__main__":
    unittest.main()
