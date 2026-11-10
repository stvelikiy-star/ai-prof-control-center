from __future__ import annotations
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import gateway_autonomous_intake as intake
import autonomous_shared_queue_worker as worker


def owner_issue(number=500):
    body = intake.MARKER + json.dumps({
        "version": 1, "profile": intake.PROFILE,
        "pilot_repository": intake.PILOT_REPOSITORY,
        "pilot_issue": intake.PILOT_ISSUE,
        "pilot_body_sha256": intake.PILOT_SHA256,
        "action": "draft-pr-only",
    }, sort_keys=True)
    return {
        "number": number,
        "title": intake.PREFIX + "Pilot task counts via Codex",
        "state": "open",
        "body": body,
        "user": {"login": "stvelikiy-star"},
    }


def owner_issue_overview(number=501):
    issue = owner_issue(number)
    payload = json.loads(issue["body"][len(intake.MARKER):])
    settings = intake.PROFILES[intake.PROFILE_OVERVIEW]
    payload["profile"] = intake.PROFILE_OVERVIEW
    payload["pilot_issue"] = settings["pilot_issue"]
    payload["pilot_body_sha256"] = settings["pilot_sha256"]
    issue["title"] = intake.PREFIX + "Pilot task overview via Codex"
    issue["body"] = intake.MARKER + json.dumps(payload, sort_keys=True)
    return issue


def owner_issue_attention(number=502):
    issue = owner_issue(number)
    payload = json.loads(issue["body"][len(intake.MARKER):])
    settings = intake.PROFILES[intake.PROFILE_ATTENTION]
    payload["profile"] = intake.PROFILE_ATTENTION
    payload["pilot_issue"] = settings["pilot_issue"]
    payload["pilot_body_sha256"] = settings["pilot_sha256"]
    issue["title"] = intake.PREFIX + "Pilot task attention via Codex"
    issue["body"] = intake.MARKER + json.dumps(payload, sort_keys=True)
    return issue


def owner_issue_real_queue(number=505):
    issue = owner_issue(number)
    payload = json.loads(issue["body"][len(intake.MARKER):])
    settings = intake.PROFILES[intake.PROFILE_REAL_QUEUE]
    payload.update({
        "profile": intake.PROFILE_REAL_QUEUE,
        "pilot_repository": settings["repository"],
        "pilot_issue": settings["pilot_issue"],
        "pilot_body_sha256": settings["pilot_sha256"],
    })
    issue["title"] = intake.PREFIX + "Control Center queue snapshot via Codex"
    issue["body"] = intake.MARKER + json.dumps(payload, sort_keys=True)
    return issue


class MainQueueIntakeTests(unittest.TestCase):
    def setUp(self):
        # The worker changes pilot executor module globals per approved profile.
        # Restore them after every fixture so the full suite is order-independent.
        names = ("REPO", "BASE", "SCOPE", "ISSUE", "DIR", "STATE", "LOCK",
                 "BRANCH", "MARKER", "EXPECTED_TITLE", "EXPECTED_BODY_SHA256",
                 "INDEPENDENT_CHECKS", "PROMPT_SUFFIX")
        original = {key: getattr(worker.core, key) for key in names}
        def restore_executor():
            for key, value in original.items():
                setattr(worker.core, key, value)
        self.addCleanup(restore_executor)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = mock.patch.object(intake, "QUEUE_ROOT", Path(self.temp.name))
        p.start()
        self.addCleanup(p.stop)

    def test_owner_contract_exact(self):
        issue = owner_issue()
        result = intake.validate_contract(issue)
        self.assertEqual(result["main_issue"], 500)
        self.assertEqual(result["profile"], intake.PROFILE)
        self.assertEqual(result["owner_issue_sha256"],
                         hashlib.sha256(issue["body"].encode()).hexdigest())

    def test_second_preapproved_profile_can_queue(self):
        contract = intake.validate_contract(owner_issue_overview())
        self.assertEqual(contract["main_issue"], 501)
        self.assertEqual(contract["pilot_issue"], 9)
        self.assertEqual(contract["profile"], intake.PROFILE_OVERVIEW)
        state = {}
        self.assertTrue(intake.process(owner_issue_overview(), state))
        self.assertEqual(state["501"]["status"], "autonomous_queued")

    def test_third_distinct_attention_profile_accepted_with_exact_scope(self):
        issue = owner_issue_attention()
        record = intake.validate_contract(issue)
        self.assertEqual(record["main_issue"], 502)
        self.assertEqual(record["pilot_issue"], 11)
        self.assertEqual(record["profile"], intake.PROFILE_ATTENTION)
        state = {}
        self.assertTrue(intake.process(issue, state))
        self.assertEqual(state["502"]["status"], "autonomous_queued")
        self.assertEqual(len(list(Path(self.temp.name).rglob("*.json"))), 1)
        tampered = owner_issue_attention(504)
        tampered["body"] = tampered["body"].replace("draft-pr-only", "merge-and-deploy")
        with self.assertRaises(intake.AutoIntakeError):
            intake.validate_contract(tampered)

    def test_real_project_owner_exact_contract_and_private_queue(self):
        issue = owner_issue_real_queue()
        verified = intake.validate_contract(issue)
        self.assertEqual(verified["pilot_repository"], "stvelikiy-star/ai-prof-control-center")
        self.assertEqual(verified["pilot_issue"], 216)
        self.assertEqual(verified["profile"], intake.PROFILE_REAL_QUEUE)
        state = {}
        self.assertTrue(intake.process(issue, state))
        self.assertEqual(state["505"]["status"], "autonomous_queued")
        saved = intake.queue_path("autonomous_pending", 505)
        self.assertEqual(json.loads(saved.read_text()), verified)
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

    def test_real_project_forbidden_repo_and_digest_and_scope_change(self):
        for key, changed in (("pilot_repository", "stvelikiy-star/resort-os"),
                             ("pilot_body_sha256", "0"*64),
                             ("action", "merge")):
            issue = owner_issue_real_queue()
            payload = json.loads(issue["body"][len(intake.MARKER):])
            payload[key] = changed
            issue["body"] = intake.MARKER + json.dumps(payload)
            with self.subTest(key=key), self.assertRaises(intake.AutoIntakeError):
                intake.validate_contract(issue)

    def test_real_project_configuration_pins_repo_branch_scope(self):
        record = intake.validate_contract(owner_issue_real_queue())
        worker.configure(record)
        self.assertEqual(worker.core.REPO, "stvelikiy-star/ai-prof-control-center")
        self.assertEqual(worker.core.BASE, "feature/ai-prof-attention-profile-20261110")
        self.assertEqual(worker.core.SCOPE, frozenset({
            "orchestrator/autonomous_queue_snapshot.py",
            "tests/test_autonomous_queue_snapshot.py",
        }))
        self.assertEqual(worker.core.ISSUE, 216)
        self.assertEqual(worker.core.EXPECTED_BODY_SHA256,
                         intake.PROFILES[intake.PROFILE_REAL_QUEUE]["pilot_sha256"])
        self.assertEqual(worker.core.INDEPENDENT_CHECKS, worker.INDEPENDENT_REAL_QUEUE)

    def test_real_project_source_issue_validates_exact_branch_and_scope(self):
        record = intake.validate_contract(owner_issue_real_queue())
        worker.configure(record)
        cfg = intake.PROFILES[intake.PROFILE_REAL_QUEUE]
        body = (cfg["pilot_marker"] + "\n"
                "Scope-Files: orchestrator/autonomous_queue_snapshot.py, tests/test_autonomous_queue_snapshot.py\n"
                "Base-Branch: feature/ai-prof-attention-profile-20261110\n")
        with mock.patch.object(worker.core, "EXPECTED_BODY_SHA256", None):
            source = {"number":216,"state":"open","user":{"login":"stvelikiy-star"},
                      "title":cfg["pilot_title"],"body":body}
            self.assertEqual(worker.core.validate_issue(source), body)
            source["body"] = body.replace("feature/ai-prof-attention-profile-20261110", "main")
            with self.assertRaises(worker.core.PilotError):
                worker.core.validate_issue(source)
            source["body"] = body.replace("tests/test_autonomous_queue_snapshot.py", "tests/test_unsafe.py")
            with self.assertRaises(worker.core.PilotError):
                worker.core.validate_issue(source)

    def test_real_project_source_record_must_match_immutable_registry(self):
        issue = owner_issue_real_queue()
        record = intake.validate_contract(issue)
        path = intake.queue_path("autonomous_pending", 505)
        intake._create_new(path, record)
        tampered = dict(record, pilot_repository="stvelikiy-star/resort-os")
        path.write_text(json.dumps(tampered))
        with mock.patch.object(worker, "get_issue") as get_issue:
            with self.assertRaises(worker.ConsumerError):
                worker.check_job(path)
            get_issue.assert_not_called()

    def test_duplicate_pilot_issue_across_different_main_issues_is_blocked(self):
        state = {}
        self.assertTrue(intake.process(owner_issue(500), state))
        self.assertTrue(intake.process(owner_issue(503), state))
        self.assertEqual(state["503"]["status"], "rejected")
        self.assertFalse(intake.locate(503))

    def test_untrusted_author_rejected(self):
        issue = owner_issue()
        issue["user"]["login"] = "attacker"
        with self.assertRaises(intake.AutoIntakeError):
            intake.validate_contract(issue)

    def test_body_mutation_rejected(self):
        issue = owner_issue()
        issue["body"] = issue["body"].replace("draft-pr-only", "deploy-production")
        with self.assertRaises(intake.AutoIntakeError):
            intake.validate_contract(issue)

    def test_intake_stores_one_private_atomic_record(self):
        state = {}
        self.assertTrue(intake.process(owner_issue(), state))
        self.assertEqual(state["500"]["status"], "autonomous_queued")
        path = intake.queue_path("autonomous_pending", 500)
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text())["pilot_issue"], 7)
        self.assertFalse(intake.process(owner_issue(), state))
        self.assertEqual(len(list(path.parent.glob("*.json"))), 1)

    def test_crash_before_state_write_reconciles_no_duplicate(self):
        issue = owner_issue()
        record = intake.validate_contract(issue)
        intake._create_new(intake.queue_path("autonomous_pending", 500), record)
        state = {}
        self.assertTrue(intake.process(issue, state))
        self.assertEqual(state["500"]["status"], "autonomous_queued")
        self.assertEqual(len(intake.locate(500)), 1)

    def test_completion_comment_updates_main_gateway_once(self):
        issue = owner_issue()
        state = {}
        self.assertTrue(intake.process(issue, state))
        pending = intake.queue_path("autonomous_pending", 500)
        completed = intake.queue_path("autonomous_completed", 500)
        completed.parent.mkdir(parents=True, exist_ok=True)
        pending.replace(completed)
        proof = {
            "pull_request": "https://github.com/stvelikiy-star/ai-prof-autonomy-pilot/pull/8",
            "commit": "a"*40,
        }
        report = Path(self.temp.name) / "proof.json"
        report.write_text(json.dumps(proof))
        with (
            mock.patch("autonomous_shared_queue_worker.verify_published", return_value=True),
            mock.patch("autonomous_shared_queue_worker.core.STATE", report),
            mock.patch.object(intake.gateway, "run_gh", return_value=[]),
            mock.patch.object(intake.gateway, "post_comment") as notify,
        ):
            self.assertTrue(intake.process(issue, state))
            self.assertEqual(state["500"]["status"], "autonomous_completed")
            notify.assert_called_once()
            self.assertFalse(intake.process(issue, state))
            notify.assert_called_once()

    def test_closed_main_issue_blocks_intake(self):
        issue = owner_issue()
        issue["state"] = "closed"
        with self.assertRaises(intake.AutoIntakeError):
            intake.validate_contract(issue)

    def test_repeated_issue_with_changed_body_rejected(self):
        issue = owner_issue()
        intake._create_new(intake.queue_path("autonomous_pending", 500), intake.validate_contract(issue))
        issue["body"] = issue["body"] + " "
        state = {}
        self.assertTrue(intake.process(issue, state))
        self.assertEqual(state["500"]["status"], "rejected")
        self.assertEqual(len(intake.locate(500)), 1)


class QueueConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        p = mock.patch.object(intake, "QUEUE_ROOT", Path(self.temp.name))
        p.start()
        self.addCleanup(p.stop)

    def setup_job(self, number=500):
        record = intake.validate_contract(owner_issue(number))
        path = intake.queue_path("autonomous_pending", number)
        intake._create_new(path, record)
        return path, record

    def test_check_job_uses_owner_and_pilot_source(self):
        path, record = self.setup_job()
        pilot = {
            "state": "open", "title": worker.PROFILE_TITLE,
            "user": {"login": "stvelikiy-star"},
            "body": worker.PROFILE_MARKER + "\nanything",
        }
        with mock.patch.object(worker, "get_issue",
                               side_effect=[owner_issue(), pilot]):
            with self.assertRaises(worker.ConsumerError):
                worker.check_job(path)

    def test_closed_owner_issue_blocks_active_task_before_publication(self):
        pending, _ = self.setup_job()
        active = worker.relocate(pending, "autonomous_active")
        closed = owner_issue()
        closed["state"] = "closed"
        with mock.patch.object(worker, "get_issue", return_value=closed):
            with self.assertRaises(intake.AutoIntakeError):
                worker.check_job(active)

    def test_changed_owner_contract_blocks_active_task(self):
        pending, _ = self.setup_job()
        active = worker.relocate(pending, "autonomous_active")
        changed = owner_issue()
        changed["body"] += "changed"
        with mock.patch.object(worker, "get_issue", return_value=changed):
            with self.assertRaises(intake.AutoIntakeError):
                worker.check_job(active)

    def test_invalid_job_is_blocked_before_execution(self):
        path, _ = self.setup_job()
        with mock.patch.object(worker, "check_job", side_effect=worker.ConsumerError("bad")), \
             mock.patch.object(worker, "run_pilot_process") as run, \
             mock.patch("sequential_job_dispatcher.resource_gate",
                        return_value=(True, "PASS")):
            self.assertEqual(worker.tick(), "INVALID_OWNER_CONTRACT_BLOCKED")
            run.assert_not_called()
            self.assertTrue(intake.queue_path("autonomous_blocked", 500).exists())

    def test_temporary_resource_gate_defers_pending(self):
        path, _ = self.setup_job()
        with mock.patch("sequential_job_dispatcher.resource_gate",
                        return_value=(False, "RAM")), \
             mock.patch.object(worker, "run_pilot_process") as run:
            self.assertEqual(worker.tick(), "RESOURCE_DEFERRED_RAM")
            run.assert_not_called()
            self.assertTrue(path.exists())

    def test_one_task_at_a_time(self):
        self.setup_job(500)
        self.setup_job(501)
        with mock.patch("sequential_job_dispatcher.resource_gate",
                        return_value=(True, "ok")), \
             mock.patch.object(worker, "check_job",
                               side_effect=lambda p: intake.validate_contract(owner_issue(int(p.stem.split("_")[-1])))), \
             mock.patch.object(worker, "run_pilot_process", return_value=0) as run, \
             mock.patch.object(worker, "verify_published", return_value=True):
            self.assertEqual(worker.tick(), "OWNER_ISSUE_COMPLETED_PR_TELEGRAM_PASS")
            run.assert_called_once()
            self.assertTrue(intake.queue_path("autonomous_completed", 500).exists())
            self.assertTrue(intake.queue_path("autonomous_pending", 501).exists())

    def test_interrupted_task_never_replays_codex(self):
        path, _ = self.setup_job()
        active = worker.relocate(path, "autonomous_active")
        with mock.patch.object(worker, "check_job", return_value=intake.validate_contract(owner_issue())), \
             mock.patch.object(worker, "verify_published", return_value=False), \
             mock.patch.object(worker, "run_pilot_process") as run:
            self.assertEqual(worker.tick(), "INTERRUPTED_BLOCKED_NO_AUTORETRY")
            run.assert_not_called()
            self.assertFalse(active.exists())
            self.assertTrue(intake.queue_path("autonomous_blocked", 500).exists())

    def test_interrupted_completed_requires_remote_proof(self):
        path, _ = self.setup_job()
        worker.relocate(path, "autonomous_active")
        with mock.patch.object(worker, "check_job", return_value=intake.validate_contract(owner_issue())), \
             mock.patch.object(worker, "verify_published", return_value=True), \
             mock.patch.object(worker, "run_pilot_process") as run:
            self.assertEqual(worker.tick(), "RECOVERED_COMPLETED_WITH_REMOTE_EVIDENCE")
            run.assert_not_called()
            self.assertTrue(intake.queue_path("autonomous_completed", 500).exists())

    def test_worker_process_group_killed_on_timeout(self):
        fake = mock.Mock()
        fake.pid = 123
        fake.communicate.side_effect = [
            worker.subprocess.TimeoutExpired("pilot", 1), ("", "")
        ]
        with mock.patch.object(worker.subprocess, "Popen", return_value=fake), \
             mock.patch.object(worker.os, "killpg") as kill:
            with self.assertRaises(worker.ConsumerError):
                worker.run_pilot_process({"main_issue": 500})
        kill.assert_called_once_with(123, worker.signal.SIGKILL)

    def test_no_work_without_pending(self):
        self.assertEqual(worker.tick(), "IDLE")


if __name__ == "__main__":
    unittest.main()
