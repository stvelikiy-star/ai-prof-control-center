from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "orchestrator"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

import github_task_gateway as gateway
import kol_publication_contract_v4 as v4
import approved_task_publisher_gate as publisher_gate


def contract() -> dict:
    return {
        "version": 4,
        "project": "kol-travel-platform",
        "title": "KÖL V4 live E2E",
        "objective": "Create one scoped KÖL source improvement and verify it fully.",
        "priority": "urgent",
        "scope": ["README.md", "docs/**", "src/**"],
        "allowed_actions": [
            "code-edit",
            "tests",
            "docs",
            "commit",
            "push",
            "pull-request",
        ],
        "forbidden_actions": [
            "merge",
            "deployment",
            "secrets",
            "destructive-operations",
            "database-mutation",
            "supabase-restore",
            "payment-activation",
            "production-change",
            "other-project-access",
            "scope-widening",
        ],
        "owner_approval_gates": [
            "No merge, deployment, database, payment or production action is authorized."
        ],
        "acceptance_criteria": [
            "All registered checks pass.",
            "One feature branch PR is created without merge or deploy.",
        ],
        "publication_action": "pull-request",
    }


def issue(number: int = 200, task: dict | None = None) -> dict:
    task = task or contract()
    return {
        "number": number,
        "title": v4.TITLE_PREFIX + task["title"],
        "body": v4.BODY_MARKER + json.dumps(task, ensure_ascii=False),
        "user": {"login": "stvelikiy-star"},
    }


def rendered_task(number: int = 200, task: dict | None = None) -> tuple[str, dict]:
    parsed = v4.parse_contract(issue(number, task))
    project = {
        "path": str(v4.PROJECT_PATH),
        "base_branch": "main",
        "agent_context": "agents/kol",
        "code_required_commands": ["git", "python3", "node", "npm", "npx"],
        "code_required_checks": [
            "npm run lint",
            "npx tsc --noEmit --incremental false",
            "npm run check:release-source",
            "npm run build",
        ],
    }
    with mock.patch.object(v4.submit, "validate_npm_run_checks"):
        text = v4.render_task(
            project,
            task_id="KOL_TRAVEL_PLATFORM_20260830T080000Z_ABCDEF",
            number=number,
            contract=parsed,
            scope=parsed["scope"],
        )
    return text, parsed


class KolPublicationContractV4Tests(unittest.TestCase):
    def test_valid_v4_is_owner_bound_and_normalized(self):
        parsed = v4.parse_contract(issue())
        self.assertEqual(parsed["version"], 4)
        self.assertEqual(parsed["project"], "kol-travel-platform")
        self.assertEqual(parsed["publication_action"], "pull-request")
        self.assertEqual(parsed["scope"], sorted(parsed["scope"]))
        self.assertTrue(v4.REQUIRED_PUBLICATION_ACTIONS.issubset(parsed["allowed_actions"]))
        self.assertTrue(v4.REQUIRED_FORBIDDEN.issubset(parsed["forbidden_actions"]))
        self.assertEqual(len(parsed["digest"]), 64)

    def test_v4_rejects_cross_project_wrong_action_and_weakened_boundary(self):
        cases = []
        cross = contract()
        cross["project"] = "ak-bermet"
        cases.append(cross)
        wrong_action = contract()
        wrong_action["publication_action"] = "deploy"
        cases.append(wrong_action)
        no_push = contract()
        no_push["allowed_actions"].remove("push")
        cases.append(no_push)
        weak = contract()
        weak["forbidden_actions"].remove("production-change")
        cases.append(weak)
        overlap = contract()
        overlap["forbidden_actions"].append("push")
        cases.append(overlap)
        unsorted = contract()
        unsorted["scope"] = ["src/**", "README.md"]
        cases.append(unsorted)
        for task in cases:
            with self.subTest(task=task):
                with self.assertRaises(gateway.GatewayError):
                    v4.parse_contract(issue(task=task))

    def test_v1_surface_cannot_be_mistaken_for_v4(self):
        v1 = {
            "number": 201,
            "title": gateway.TITLE_PREFIX + "Legacy",
            "body": gateway.BODY_MARKER + "{}",
            "user": {"login": "stvelikiy-star"},
        }
        self.assertFalse(v4.is_v4_issue(v1))
        with self.assertRaises(gateway.GatewayError):
            v4.parse_contract(v1)

    def test_rendered_task_carries_exact_publication_authority(self):
        text, parsed = rendered_task()
        self.assertIn("Publication-Contract-Version: 4", text)
        self.assertIn("Publication-Action: pull-request", text)
        self.assertIn("Publication-Source-Issue: 200", text)
        self.assertIn("Publication-Repository: stvelikiy-star/kol-travel-platform", text)
        self.assertIn(f"Publication-Contract-Digest: {parsed['digest']}", text)
        self.assertNotIn("Out-of-Scope: All files outside Scope-Files; commit, push", text)
        self.assertIn("Out-of-Scope: All files outside Scope-Files; merge, deployment", text)

    def test_publisher_revalidates_exact_source_issue_before_publication(self):
        text, parsed = rendered_task()
        payload = issue(200)
        result = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with mock.patch.object(publisher_gate.publisher, "run", return_value=result) as run:
            publisher_gate._validate_v4_authority(text)
        run.assert_called_once()
        argv = run.call_args.args[0]
        self.assertIn("repos/stvelikiy-star/ai-prof-control-center/issues/200", argv)
        metadata = v4.parse_task_publication_metadata(text)
        self.assertEqual(metadata["digest"], parsed["digest"])

    def test_publisher_rejects_source_digest_or_scope_mismatch(self):
        text, _parsed = rendered_task()
        bad_source = contract()
        bad_source["objective"] = "Different but syntactically valid objective."
        payload = issue(200, bad_source)
        result = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with mock.patch.object(publisher_gate.publisher, "run", return_value=result):
            with self.assertRaisesRegex(
                publisher_gate.publisher.PublisherError,
                "digest mismatch",
            ):
                publisher_gate._validate_v4_authority(text)

    def test_publisher_rejects_legacy_task_before_any_github_or_git_publication(self):
        legacy = "\n".join(
            [
                "Task-ID: KOL_TRAVEL_PLATFORM_20260829T095522Z_CFB967",
                "Project-Path: /home/agent/Загрузки/kol-travel-platform",
                "Base-Branch: main",
                "Work-Branch: feature/chatgpt-issue-163",
                "Goal: legacy",
                "Instructions: Source: authorized private GitHub task issue #163.",
                "Scope-Files: README.md",
            ]
        )
        with mock.patch.object(publisher_gate.publisher, "run") as run:
            with self.assertRaises(gateway.GatewayError):
                publisher_gate._validate_v4_authority(legacy)
        run.assert_not_called()

    def test_wildcard_scope_matches_descendants_without_literal_star_directory(self):
        project = Path("/tmp/does-not-need-to-exist")
        self.assertTrue(
            publisher_gate._v4_path_in_scope(
                project, "docs/reports/a.md", ["docs/**"]
            )
        )
        self.assertTrue(
            publisher_gate._v4_path_in_scope(
                project, "src/lib/a.ts", ["README.md", "src/**"]
            )
        )
        self.assertFalse(
            publisher_gate._v4_path_in_scope(
                project, "scripts/a.mjs", ["src/**"]
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
