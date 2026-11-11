#!/usr/bin/env python3
"""Owner-only, exact-contract autonomous intake into AI PROF shared queue lane.

Existing V1-V4 intake and queue files are unchanged. Only a fixed pilot profile
is permitted here; no user-supplied commands, project paths or branch names.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import github_task_gateway as gateway
import atomic_route_guard as route

PREFIX = "[AI-PROF-AUTO] "
MARKER = "AI-PROF-AUTO-V1\n"
PROFILE = "pilot-task-counts-v1"
PILOT_REPOSITORY = "stvelikiy-star/ai-prof-autonomy-pilot"
PILOT_ISSUE = 7
PILOT_SHA256 = "52f85f6d83454bd8ca80aa4b0c58881dc0327c0369cd003154fb3f6bf05dd057"
PROFILE_OVERVIEW = "pilot-task-overview-v1"
PROFILE_ATTENTION = "pilot-task-attention-v1"
PROFILE_REAL_QUEUE = "control-center-queue-snapshot-v1"
PROFILES = {
    PROFILE: {
        "pilot_issue": PILOT_ISSUE,
        "pilot_sha256": PILOT_SHA256,
        "pilot_title": "[AI-PROF-PILOT] Add deterministic task result counts",
        "pilot_marker": "AI-PROF-PILOT-E2E-V4",
    },
    PROFILE_OVERVIEW: {
        "pilot_issue": 9,
        "pilot_sha256": "56914000f1de784b3df978f48f6a4a32a8954d3566c7f0f23669f440c968109a",
        "pilot_title": "[AI-PROF-PILOT] Add task overview from checks",
        "pilot_marker": "AI-PROF-PILOT-E2E-V5",
    },
    PROFILE_ATTENTION: {
        "pilot_issue": 11,
        "pilot_sha256": "ffbdabdc22825b2446453b1c13d3f94d736493510719f91355883693a39d64e3",
        "pilot_title": "[AI-PROF-PILOT] Add deterministic attention summary",
        "pilot_marker": "AI-PROF-PILOT-E2E-V6",
    },
    PROFILE_REAL_QUEUE: {
        "pilot_issue": 216,
        "pilot_sha256": "ae8c1fee00529f6145fbf0d36014d7529a1bc1617b835aad464d182a7b7c466c",
        "pilot_title": "[AI-PROF-REAL] Add read-only autonomous queue snapshot",
        "pilot_marker": "AI-PROF-REAL-E2E-V1",
        "repository": "stvelikiy-star/ai-prof-control-center",
        "base_branch": "feature/ai-prof-attention-profile-20261110",
        "base_sha": "946e8ffc0c0eb2697f5e01bdb999e820941a56d1",
        "scope": ("orchestrator/autonomous_queue_snapshot.py", "tests/test_autonomous_queue_snapshot.py"),
    },
}
QUEUE_ROOT = gateway.STATE_ROOT / "queue"
QUEUE_NAMES = ("autonomous_pending", "autonomous_active", "autonomous_completed", "autonomous_blocked")
RECORD_MARKER = "ai-prof-auto-v1"
MAX_RECORD_BYTES = 4096


class AutoIntakeError(gateway.GatewayError):
    pass


def is_autonomous(issue: dict) -> bool:
    return isinstance(issue.get("title"), str) and issue["title"].startswith(PREFIX)


def validate_contract(issue: dict) -> dict:
    if not gateway.authorized_issue(issue):
        raise AutoIntakeError("Owner identity not authorized")
    number = gateway.issue_number(issue)
    if issue.get("state") != "open":
        raise AutoIntakeError("Owner issue is not open")
    titles = {
        PROFILE: PREFIX + "Pilot task counts via Codex",
        PROFILE_OVERVIEW: PREFIX + "Pilot task overview via Codex",
        PROFILE_ATTENTION: PREFIX + "Pilot task attention via Codex",
        PROFILE_REAL_QUEUE: PREFIX + "Control Center queue snapshot via Codex",
    }
    if issue.get("title") not in titles.values():
        raise AutoIntakeError("Unexpected autonomous issue title")
    body = issue.get("body")
    if not isinstance(body, str) or not body.startswith(MARKER) or len(body) > 1800:
        raise AutoIntakeError("Invalid owner contract marker")
    try:
        payload = json.loads(body[len(MARKER):])
    except (ValueError, TypeError) as exc:
        raise AutoIntakeError("Malformed JSON contract") from exc
    profile = payload.get("profile") if isinstance(payload, dict) else None
    settings = PROFILES.get(profile)
    if settings is None or issue.get("title") != titles.get(profile):
        raise AutoIntakeError("Profile not in fixed approved registry")
    expected = {
        "version": 1, "profile": profile,
        "pilot_repository": settings.get("repository", PILOT_REPOSITORY),
        "pilot_issue": settings["pilot_issue"],
        "pilot_body_sha256": settings["pilot_sha256"],
        "action": "draft-pr-only",
    }
    if payload != expected:
        raise AutoIntakeError("Contract differs from reviewed immutable profile")
    return {
        "version": 1, "kind": RECORD_MARKER, "main_issue": number,
        "profile": profile, "pilot_issue": settings["pilot_issue"],
        "pilot_repository": settings.get("repository", PILOT_REPOSITORY),
        "pilot_sha256": settings["pilot_sha256"],
        "owner_issue_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "deadline_seconds": 4 * 3600,
    }


def queue_path(queue: str, issue_id: int) -> Path:
    if queue not in QUEUE_NAMES or not isinstance(issue_id, int) or issue_id < 1:
        raise AutoIntakeError("Unsafe queue path")
    return QUEUE_ROOT / queue / f"AI_PROF_AUTO_MAIN_{issue_id}.json"


def locate(issue_id: int) -> list[Path]:
    found = [queue_path(name, issue_id) for name in QUEUE_NAMES]
    return [path for path in found if path.is_file() and not path.is_symlink()]


def _create_new(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise AutoIntakeError("Queue path symlink")
    raw = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if len(raw) > MAX_RECORD_BYTES:
        raise AutoIntakeError("Queue record too large")
    fd, tmp = tempfile.mkstemp(prefix=".auto-intake-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise AutoIntakeError("Duplicate task queue record") from exc
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(tmp)


def report_existing(issue: dict, record: dict) -> bool:
    if record.get("kind") != RECORD_MARKER:
        return False
    if record.get("status") in ("autonomous_completed", "autonomous_blocked"):
        return False
    number = gateway.issue_number(issue)
    files = locate(number)
    if len(files) != 1:
        return False
    path = files[0]
    if path.parent.name not in ("autonomous_completed", "autonomous_blocked"):
        return False
    valid = validate_contract(issue)
    if record.get("contract_sha256") != valid["owner_issue_sha256"]:
        raise AutoIntakeError("Gateway record does not match original owner contract")
    if path.parent.name == "autonomous_completed":
        import autonomous_shared_queue_worker as consumer
        if not consumer.verify_published(valid):
            return False
        proof = json.loads(consumer.core.STATE.read_text(encoding="utf-8"))
        body = (
            f"AI-PROF-AUTO-RESULT-V1 issue={number}\n"
            "Статус: PASS — автономное задание выполнено.\n"
            f"Draft PR: {proof['pull_request']}\n"
            f"SHA: {proof['commit']}\n"
            "Telegram: уведомление доставлено.\n"
            "Слияние и развёртывание не выполнялись."
        )
        status = "autonomous_completed"
    else:
        body = (
            f"AI-PROF-AUTO-RESULT-V1 issue={number}\n"
            "Статус: BLOCKED — выполнение остановлено; "
            "повторный Codex без проверки владельца запрещён."
        )
        status = "autonomous_blocked"
    comments = gateway.run_gh([
        "api", "-X", "GET",
        f"repos/{gateway.REPOSITORY}/issues/{number}/comments?per_page=100",
    ], timeout=40)
    if not isinstance(comments, list):
        raise AutoIntakeError("Unable to verify existing issue comments")
    marker = f"AI-PROF-AUTO-RESULT-V1 issue={number}"
    if not any(isinstance(c, dict) and
               isinstance(c.get("body"), str) and marker in c["body"]
               for c in comments):
        gateway.post_comment(number, body)
    record["status"] = status
    record["result_reported"] = True
    return True


def _process_when_authorized_route(issue: dict, issues_state: dict) -> bool:
    number = gateway.issue_number(issue)
    key = str(number)
    if key in issues_state:
        existing = issues_state[key]
        return report_existing(issue, existing) if isinstance(existing, dict) else False
    try:
        record = validate_contract(issue)
        existing = locate(number)
        if len(existing) > 1:
            raise AutoIntakeError("Multiple queue records for issue")
        if existing:
            saved = json.loads(existing[0].read_text(encoding="utf-8"))
            if any(saved.get(k) != v for k, v in record.items()):
                raise AutoIntakeError("Existing queue record does not match contract")
        else:
            # Never authorize the same pilot issue twice, even from different
            # main repository issues. One reviewed issue -> at most one PR.
            for queue in QUEUE_NAMES:
                folder = QUEUE_ROOT / queue
                for prior in sorted(folder.glob("AI_PROF_AUTO_MAIN_*.json")):
                    if prior.is_symlink() or prior.stat().st_size > MAX_RECORD_BYTES:
                        raise AutoIntakeError("Unsafe prior autonomous record")
                    prior_data = json.loads(prior.read_text(encoding="utf-8"))
                    if (prior_data.get("pilot_issue") == record["pilot_issue"]
                            and prior_data.get("main_issue") != number):
                        raise AutoIntakeError("Pilot issue already assigned to another task")
            _create_new(queue_path("autonomous_pending", number), record)
    except (AutoIntakeError, ValueError, OSError) as exc:
        issues_state[key] = {
            "status": "rejected", "kind": RECORD_MARKER,
            "code": gateway.sanitize(exc, 220),
        }
        return True
    issues_state[key] = {
        "status": "autonomous_queued", "kind": RECORD_MARKER,
        "auto_queue_id": f"AI_PROF_AUTO_MAIN_{number}",
        "contract_sha256": record["owner_issue_sha256"],
    }
    return True


def process(issue: dict, issues_state: dict) -> bool:
    """A single route gate serializes GitHub intake with a future cutover.

    Frozen means DEFER, not reject: do not record this issue as terminal.
    """
    try:
        with route.gate():
            selected = route.mode()
            if selected == 'frozen':
                return False
            if selected == 'fifo':
                import global_fifo_owner_runtime as fifo_runtime
                return fifo_runtime.process_issue(issue, issues_state)
            if selected == 'legacy':
                return _process_when_authorized_route(issue, issues_state)
            raise route.RouteError('unsupported autonomous route')
    except route.RouteError as exc:
        raise AutoIntakeError('autonomous routing cannot be verified') from exc
