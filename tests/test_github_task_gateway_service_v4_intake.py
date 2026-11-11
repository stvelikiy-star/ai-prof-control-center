from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

import github_task_gateway as gateway
import github_task_gateway_service_v4 as service_v4
import kol_publication_contract_v4 as v4
import submit_task as submit


def contract() -> dict:
    return {
        "version": 4,
        "project": "kol-travel-platform",
        "title": "Harden deployment safety self-test diagnostics",
        "objective": "Strengthen one bounded KÖL safety self-test without production changes.",
        "priority": "high",
        "scope": ["scripts/check-deployment-env-selftest.mjs"],
        "allowed_actions": ["code-edit", "commit", "pull-request", "push", "tests"],
        "forbidden_actions": [
            "database-mutation",
            "deployment",
            "destructive-operations",
            "merge",
            "other-project-access",
            "payment-activation",
            "production-change",
            "scope-widening",
            "secrets",
            "supabase-restore",
        ],
        "owner_approval_gates": ["No production authority."],
        "acceptance_criteria": ["Registered checks pass."],
        "publication_action": "pull-request",
    }


def issue(number: int = 172) -> dict:
    task = contract()
    return {
        "number": number,
        "title": v4.TITLE_PREFIX + task["title"],
        "body": v4.BODY_MARKER + json.dumps(task, ensure_ascii=False),
        "user": {"login": "stvelikiy-star"},
    }


class KolV4GatewayIntakeBoundaryTests(unittest.TestCase):
    def test_missing_imported_task_reports_orphan_once_without_duplicate(self):
        record = {"task_id": "KOL_TRAVEL_PLATFORM_20260830T142940Z_C13938",
                  "status": "imported", "last_reported_state": "not found",
                  "last_reported_reason": ""}
        state = {"172": record}
        with (
            mock.patch.object(gateway, "task_public_state", return_value=("not found", "")),
            mock.patch.object(gateway, "post_comment") as post,
            mock.patch.object(v4, "submit_contract") as submit_contract,
        ):
            self.assertTrue(service_v4.process_kol_v4_issue(issue(), state))
            self.assertEqual(state["172"]["status"], "orphaned")
            self.assertIn("создание запрещено", post.call_args.args[1].lower())
            self.assertFalse(service_v4.process_kol_v4_issue(issue(), state))
            post.assert_called_once()
            submit_contract.assert_not_called()

    def test_existing_task_preserves_normal_reporting(self):
        state = {"172": {"task_id": "KOL_TRAVEL_PLATFORM_20260830T142940Z_C13938"}}
        with (
            mock.patch.object(gateway, "task_public_state", return_value=("approved", "")),
            mock.patch.object(gateway, "report_task_state", return_value=True) as report,
        ):
            self.assertTrue(service_v4.process_kol_v4_issue(issue(), state))
            report.assert_called_once()

    def test_expected_intake_rejection_does_not_escape_and_kill_gateway(self):
        issues_state: dict = {}
        failure = submit.IntakeError(
            "project configuration error for kol-travel-platform: "
            "code_required_checks references missing npm script: check:release-source"
        )

        with (
            mock.patch.object(gateway, "find_existing_task_for_issue", return_value=None),
            mock.patch.object(v4, "submit_contract", side_effect=failure),
            mock.patch.object(gateway, "post_comment") as post_comment,
        ):
            changed = service_v4.process_kol_v4_issue(issue(), issues_state)

        self.assertTrue(changed)
        self.assertEqual(issues_state["172"]["status"], "rejected")
        self.assertEqual(issues_state["172"]["kind"], v4.RECORD_KIND)
        self.assertIn("check:release-source", issues_state["172"]["code"])
        post_comment.assert_called_once()
        self.assertEqual(post_comment.call_args.args[0], 172)
        self.assertIn("AI PROF KÖL V4 rejected", post_comment.call_args.args[1])
        self.assertIn("No task was enqueued", post_comment.call_args.args[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
