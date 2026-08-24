from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "orchestrator" / "telegram_bridge.py"
GATEWAY_PATH = ROOT / "orchestrator" / "github_task_gateway.py"

legacy_spec = importlib.util.spec_from_file_location("telegram_bridge", LEGACY_PATH)
if legacy_spec is None or legacy_spec.loader is None:
    raise RuntimeError("cannot load Telegram bridge")
legacy = importlib.util.module_from_spec(legacy_spec)
sys.modules["telegram_bridge"] = legacy
legacy_spec.loader.exec_module(legacy)

gateway_spec = importlib.util.spec_from_file_location(
    "ai_prof_github_task_gateway", GATEWAY_PATH
)
if gateway_spec is None or gateway_spec.loader is None:
    raise RuntimeError("cannot load GitHub task gateway")
gateway = importlib.util.module_from_spec(gateway_spec)
sys.modules[gateway_spec.name] = gateway
gateway_spec.loader.exec_module(gateway)


def contract(title: str = "Gateway test") -> dict:
    return {
        "version": 1,
        "project": "ai-prof-control-center",
        "title": title,
        "objective": "Implement only the explicitly scoped mobile control change.",
        "priority": "normal",
        "scope": ["orchestrator/telegram_bridge.py"],
        "allowed_actions": ["code-edit", "tests"],
        "forbidden_actions": [
            "commit",
            "push",
            "merge",
            "deployment",
            "secrets",
            "destructive-operations",
        ],
        "owner_approval_gates": [
            "production activation requires separate owner approval"
        ],
        "acceptance_criteria": [
            "tests pass",
            "existing safety remains fail-closed",
        ],
    }


def health_contract(title: str = "Control Center health") -> dict:
    task = contract(title)
    task.update(
        {
            "version": 2,
            "objective": "Run the registered read-only Control Center health check.",
            "scope": ["reports/AI_PROF_RUNTIME_HEALTH.md"],
            "allowed_actions": ["tests"],
            "execution_mode": "operations",
            "operation_profile": gateway.HEALTH_OPERATION_PROFILE,
        }
    )
    return task


def issue(
    number: int = 3,
    *,
    login: str = "stvelikiy-star",
    task: dict | None = None,
) -> dict:
    task = task or contract()
    return {
        "number": number,
        "title": gateway.TITLE_PREFIX + task["title"],
        "body": gateway.BODY_MARKER + json.dumps(task),
        "user": {"login": login},
    }


class GitHubTaskGatewayTests(unittest.TestCase):
    def test_v1_is_hard_bound_to_private_control_repository_and_owner(self):
        self.assertEqual(
            gateway.REPOSITORY, "stvelikiy-star/ai-prof-control-center"
        )
        self.assertEqual(gateway.OWNER_LOGINS, frozenset({"stvelikiy-star"}))
        source = GATEWAY_PATH.read_text(encoding="utf-8")
        self.assertNotIn("AI_PROF_GITHUB_TASK_REPOSITORY", source)
        self.assertNotIn("AI_PROF_GITHUB_OWNER_LOGINS", source)

    def test_valid_contract_is_strictly_parsed(self):
        parsed = gateway.parse_contract(issue())
        self.assertEqual(parsed["project"], "ai-prof-control-center")
        self.assertEqual(parsed["priority"], "normal")
        self.assertEqual(parsed["allowed_actions"], ["code-edit", "tests"])
        self.assertTrue(
            gateway.REQUIRED_FORBIDDEN.issubset(
                set(parsed["forbidden_actions"])
            )
        )

    def test_v2_accepts_only_registered_read_only_health_profile(self):
        parsed = gateway.parse_contract(issue(task=health_contract()))
        self.assertEqual(parsed["version"], 2)
        self.assertEqual(parsed["execution_mode"], "operations")
        self.assertEqual(
            parsed["operation_profile"], gateway.HEALTH_OPERATION_PROFILE
        )

        cases = []
        unknown = health_contract()
        unknown["operation_profile"] = "unknown-profile"
        cases.append(unknown)
        cross_project = health_contract()
        cross_project["project"] = "ak-bermet"
        cases.append(cross_project)
        broad = health_contract()
        broad["allowed_actions"] = ["tests", "code-edit"]
        cases.append(broad)
        code_with_profile = health_contract()
        code_with_profile["execution_mode"] = "code"
        cases.append(code_with_profile)
        for task in cases:
            with self.subTest(task=task):
                with self.assertRaises(gateway.GatewayError):
                    gateway.parse_contract(issue(task=task))

    def test_v2_requires_exact_mode_profile_keys(self):
        missing = health_contract()
        missing.pop("operation_profile")
        with self.assertRaisesRegex(gateway.GatewayError, "keys mismatch"):
            gateway.parse_contract(issue(task=missing))
        extra = health_contract()
        extra["command"] = "id"
        with self.assertRaisesRegex(gateway.GatewayError, "keys mismatch"):
            gateway.parse_contract(issue(task=extra))

    def test_contract_rejects_extra_key_and_title_mismatch(self):
        extra = contract()
        extra["shell"] = "id"
        with self.assertRaises(gateway.GatewayError):
            gateway.parse_contract(issue(task=extra))
        mismatched = issue(task=contract("Contract title"))
        mismatched["title"] = gateway.TITLE_PREFIX + "Different title"
        with self.assertRaisesRegex(gateway.GatewayError, "do not match"):
            gateway.parse_contract(mismatched)

    def test_contract_cannot_request_shell_or_deployment_authority(self):
        unsafe = contract()
        unsafe["allowed_actions"].append("shell")
        with self.assertRaisesRegex(
            gateway.GatewayError, "unsupported authority"
        ):
            gateway.parse_contract(issue(task=unsafe))
        weak = contract()
        weak["forbidden_actions"].remove("deployment")
        with self.assertRaisesRegex(gateway.GatewayError, "safety boundary"):
            gateway.parse_contract(issue(task=weak))

    def test_contract_rejects_secret_like_values_before_enqueue(self):
        unsafe = contract()
        unsafe["objective"] = "Use password=SUPERSECRET to finish the task"
        with self.assertRaisesRegex(
            gateway.GatewayError, "secret-like material"
        ):
            gateway.parse_contract(issue(task=unsafe))

    def test_authorization_requires_exact_owner_and_rejects_prs(self):
        self.assertTrue(gateway.authorized_issue(issue()))
        self.assertFalse(
            gateway.authorized_issue(issue(login="someone-else"))
        )
        pr = issue()
        pr["pull_request"] = {"url": "private"}
        self.assertFalse(gateway.authorized_issue(pr))

    def test_work_branch_is_deterministic_and_not_issue_controlled(self):
        self.assertEqual(gateway.work_branch(55), "feature/chatgpt-issue-55")

    def test_submit_contract_calls_validator_without_shell(self):
        result = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "task_id": (
                        "AI_PROF_CONTROL_CENTER_20260812T100000Z_ABCDEF"
                    ),
                    "queue": "pending",
                }
            ),
            "",
        )
        with mock.patch.object(
            gateway.subprocess, "run", return_value=result
        ) as run:
            created = gateway.submit_contract(
                44, gateway.parse_contract(issue(44))
            )
        self.assertEqual(created["queue"], "pending")
        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertIn(str(gateway.SUBMIT_TASK), argv)
        self.assertIn("feature/chatgpt-issue-44", argv)
        self.assertFalse(kwargs.get("shell", False))
        self.assertNotIn("sh", argv)
        self.assertNotIn("bash", argv)

    def test_v2_submit_selects_operations_without_shell(self):
        result = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "task_id": (
                        "AI_PROF_CONTROL_CENTER_20260824T100000Z_ABCDEF"
                    ),
                    "queue": "pending",
                }
            ),
            "",
        )
        parsed = gateway.parse_contract(issue(45, task=health_contract()))
        with mock.patch.object(
            gateway.subprocess, "run", return_value=result
        ) as run:
            gateway.submit_contract(45, parsed)
        argv = run.call_args.args[0]
        self.assertIn("--execution-mode", argv)
        self.assertEqual(argv[argv.index("--execution-mode") + 1], "operations")
        self.assertIn("--operation-profile", argv)
        self.assertEqual(
            argv[argv.index("--operation-profile") + 1],
            gateway.HEALTH_OPERATION_PROFILE,
        )
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_v1_submit_argv_remains_code_default(self):
        result = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "task_id": (
                        "AI_PROF_CONTROL_CENTER_20260824T100001Z_ABCDEF"
                    ),
                    "queue": "pending",
                }
            ),
            "",
        )
        parsed = gateway.parse_contract(issue(46))
        with mock.patch.object(
            gateway.subprocess, "run", return_value=result
        ) as run:
            gateway.submit_contract(46, parsed)
        argv = run.call_args.args[0]
        self.assertNotIn("--execution-mode", argv)
        self.assertNotIn("--operation-profile", argv)

    def test_same_issue_is_imported_only_once_with_persisted_state(self):
        state: dict[str, dict] = {}
        created = {
            "task_id": "AI_PROF_CONTROL_CENTER_20260812T100000Z_ABCDEF",
            "queue": "pending",
        }
        with mock.patch.object(
            gateway, "find_existing_task_for_issue", return_value=None
        ), mock.patch.object(
            gateway, "submit_contract", return_value=created
        ) as submit, mock.patch.object(gateway, "post_comment") as comment:
            self.assertTrue(gateway.process_issue(issue(77), state))
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(state["77"]["task_id"], created["task_id"])
        self.assertTrue(comment.called)

        with mock.patch.object(gateway, "submit_contract") as submit_again, \
             mock.patch.object(
                 gateway, "task_public_state", return_value=("queued", "")
             ), \
             mock.patch.object(gateway, "post_comment"):
            gateway.process_issue(issue(77), state)
        submit_again.assert_not_called()

    def test_crash_recovery_finds_existing_task_and_never_resubmits(self):
        task_id = "AI_PROF_CONTROL_CENTER_20260812T100001Z_ABCDEF"
        with tempfile.TemporaryDirectory(prefix="ai-prof-gateway-dedupe-") as tmp:
            state_root = Path(tmp)
            pending = state_root / "queue" / "pending"
            pending.mkdir(parents=True)
            (pending / f"{task_id}.md").write_text(
                "Task-ID: "
                + task_id
                + "\nInstructions: test\n"
                + gateway.issue_marker(91)
                + "\n",
                encoding="utf-8",
            )
            state: dict[str, dict] = {}
            with mock.patch.object(gateway, "STATE_ROOT", state_root), \
                 mock.patch.object(gateway, "submit_contract") as submit, \
                 mock.patch.object(gateway, "post_comment") as comment:
                self.assertTrue(gateway.process_issue(issue(91), state))
            submit.assert_not_called()
            self.assertEqual(state["91"]["task_id"], task_id)
            self.assertEqual(state["91"]["queue"], "pending")
            message = comment.call_args.args[1]
            self.assertIn("No duplicate task was created", message)

    def test_multiple_recovery_matches_fail_closed(self):
        with tempfile.TemporaryDirectory(
            prefix="ai-prof-gateway-dedupe-conflict-"
        ) as tmp:
            state_root = Path(tmp)
            for queue, task_id in (
                ("pending", "TASK_ONE_20260812_ABCDEF"),
                ("blocked", "TASK_TWO_20260812_ABCDEF"),
            ):
                directory = state_root / "queue" / queue
                directory.mkdir(parents=True)
                (directory / f"{task_id}.md").write_text(
                    gateway.issue_marker(92) + "\n", encoding="utf-8"
                )
            with mock.patch.object(gateway, "STATE_ROOT", state_root):
                with self.assertRaisesRegex(
                    gateway.GatewayError, "multiple AI PROF tasks"
                ):
                    gateway.find_existing_task_for_issue(92)

    def test_unauthorized_author_is_rejected_without_enqueue(self):
        state: dict[str, dict] = {}
        with mock.patch.object(gateway, "submit_contract") as submit, \
             mock.patch.object(gateway, "post_comment") as comment:
            self.assertTrue(
                gateway.process_issue(issue(88, login="intruder"), state)
            )
        submit.assert_not_called()
        self.assertEqual(state["88"]["code"], "UNAUTHORIZED_AUTHOR")
        self.assertTrue(comment.called)

    def test_comments_are_secret_redacted_before_github_call(self):
        with mock.patch.object(gateway, "run_gh") as run:
            gateway.post_comment(
                3, "password=SUPERSECRET token=ANOTHERTOKEN normal"
            )
        argv = run.call_args.args[0]
        body_arg = next(item for item in argv if item.startswith("body="))
        self.assertNotIn("SUPERSECRET", body_arg)
        self.assertNotIn("ANOTHERTOKEN", body_arg)
        self.assertIn("[REDACTED]", body_arg)

    def test_gateway_state_is_atomic_and_mode_0600(self):
        with tempfile.TemporaryDirectory(
            prefix="ai-prof-gateway-state-"
        ) as tmp:
            path = Path(tmp) / "nested" / "state.json"
            payload = {
                "version": 1,
                "issues": {"3": {"status": "rejected"}},
            }
            gateway.save_state(payload, path)
            self.assertEqual(gateway.load_state(path), payload)
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_gh_runner_uses_argv_not_shell_and_disables_prompts(self):
        completed = subprocess.CompletedProcess([], 0, "{}", "")
        with mock.patch.object(
            gateway.shutil, "which", return_value="/usr/bin/gh"
        ), mock.patch.object(
            gateway.subprocess, "run", return_value=completed
        ) as run:
            gateway.run_gh(
                ["api", "repos/stvelikiy-star/ai-prof-control-center"]
            )
        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(argv[0], "/usr/bin/gh")
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["env"]["GH_PROMPT_DISABLED"], "1")

    def test_systemd_gateway_is_hardened_and_has_no_secret_environment(self):
        unit = (
            ROOT / "systemd" / "ai-prof-github-task-gateway.service"
        ).read_text(encoding="utf-8")
        self.assertIn("github_task_gateway.py --poll-seconds 60", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ProtectHome=read-only", unit)
        self.assertIn(
            "ReadWritePaths=/home/agent/.local/state/ai-prof-control-center",
            unit,
        )
        self.assertNotIn("TOKEN=", unit)
        self.assertNotIn("PASSWORD=", unit)
        self.assertNotIn("SECRET=", unit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
