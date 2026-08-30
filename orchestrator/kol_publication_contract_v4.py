#!/usr/bin/env python3
"""Strict KÖL V4 task contract for bounded feature-branch PR publication.

V4 is deliberately separate from the generic V1 code-task authority. It may
prepare one reviewed KÖL feature branch through commit -> push -> pull request,
but it never grants merge, deployment, database, secret, payment, production,
or cross-project authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import github_task_gateway as gateway
import submit_task as submit

VERSION = 4
PROJECT = "kol-travel-platform"
PROJECT_PATH = Path("/home/agent/Загрузки/kol-travel-platform")
REPOSITORY = "stvelikiy-star/kol-travel-platform"
TITLE_PREFIX = "[AI-PROF-KOL-TASK] "
BODY_MARKER = "AI-PROF-KOL-TASK-V4\n"
PUBLICATION_ACTION = "pull-request"
RECORD_KIND = "kol-publication-v4"
CONTRACT_KEYS = {
    "version",
    "project",
    "title",
    "objective",
    "priority",
    "scope",
    "allowed_actions",
    "forbidden_actions",
    "owner_approval_gates",
    "acceptance_criteria",
    "publication_action",
}
ALLOWED_ACTIONS = {
    "code-edit",
    "tests",
    "docs",
    "commit",
    "push",
    "pull-request",
}
REQUIRED_PUBLICATION_ACTIONS = {"commit", "push", "pull-request"}
REQUIRED_FORBIDDEN = {
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
}


class KolV4Error(gateway.GatewayError):
    pass


def is_v4_issue(issue: dict[str, Any]) -> bool:
    title = issue.get("title")
    return isinstance(title, str) and title.startswith(TITLE_PREFIX)


def _contract_digest(contract: dict[str, Any]) -> str:
    payload = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_contract(issue: dict[str, Any]) -> dict[str, Any]:
    if not gateway.authorized_issue(issue):
        raise KolV4Error("KÖL V4 requires the fixed owner-authored issue")

    issue_title = issue.get("title")
    body = issue.get("body")
    if not isinstance(issue_title, str) or not issue_title.startswith(TITLE_PREFIX):
        raise KolV4Error("missing KÖL V4 task title marker")
    if (
        not isinstance(body, str)
        or not body.startswith(BODY_MARKER)
        or len(body) > gateway.MAX_BODY
    ):
        raise KolV4Error("missing or invalid KÖL V4 body marker")
    try:
        raw = json.loads(body[len(BODY_MARKER) :])
    except json.JSONDecodeError as exc:
        raise KolV4Error("KÖL V4 body is not valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != CONTRACT_KEYS:
        raise KolV4Error("KÖL V4 contract keys must match the fixed schema")
    if raw.get("version") != VERSION:
        raise KolV4Error("unsupported KÖL publication contract version")

    project = gateway._text("project", raw["project"], limit=80)
    if project != PROJECT:
        raise KolV4Error("KÖL V4 is restricted to kol-travel-platform")
    title = gateway._text("title", raw["title"], limit=120)
    if issue_title[len(TITLE_PREFIX) :].strip() != title:
        raise KolV4Error("issue title and KÖL V4 title do not match")
    objective = gateway._text("objective", raw["objective"])
    priority = gateway._text("priority", raw["priority"], limit=20)
    if priority not in gateway.ALLOWED_PRIORITIES:
        raise KolV4Error("invalid KÖL V4 priority")

    scope = gateway._string_list("scope", raw["scope"], max_items=gateway.MAX_SCOPE)
    if scope != sorted(set(scope)):
        raise KolV4Error("KÖL V4 scope must be unique and sorted")

    allowed = set(
        gateway._string_list("allowed_actions", raw["allowed_actions"], max_items=10)
    )
    if not allowed.issubset(ALLOWED_ACTIONS):
        raise KolV4Error("KÖL V4 allowed_actions contains unsupported authority")
    if not REQUIRED_PUBLICATION_ACTIONS.issubset(allowed):
        raise KolV4Error("KÖL V4 requires commit, push and pull-request authority")

    forbidden = set(
        gateway._string_list("forbidden_actions", raw["forbidden_actions"], max_items=20)
    )
    if not REQUIRED_FORBIDDEN.issubset(forbidden):
        raise KolV4Error("KÖL V4 forbidden_actions weakens the publication boundary")
    if allowed & forbidden:
        raise KolV4Error("KÖL V4 action cannot be both allowed and forbidden")

    gates = gateway._string_list(
        "owner_approval_gates", raw["owner_approval_gates"], max_items=20
    )
    acceptance = gateway._string_list(
        "acceptance_criteria", raw["acceptance_criteria"], max_items=20
    )
    publication_action = gateway._text(
        "publication_action", raw["publication_action"], limit=30
    )
    if publication_action != PUBLICATION_ACTION:
        raise KolV4Error("KÖL V4 publication_action must be pull-request")

    normalized = {
        "version": VERSION,
        "project": project,
        "title": title,
        "objective": objective,
        "priority": priority,
        "scope": scope,
        "allowed_actions": sorted(allowed),
        "forbidden_actions": sorted(forbidden),
        "owner_approval_gates": gates,
        "acceptance_criteria": acceptance,
        "publication_action": publication_action,
    }
    normalized["digest"] = _contract_digest({key: normalized[key] for key in CONTRACT_KEYS})
    return normalized


def render_instructions(number: int, contract: dict[str, Any]) -> str:
    text = "; ".join(
        [
            contract["objective"],
            gateway.issue_marker(number),
            f"Priority: {contract['priority']}.",
            "Allowed actions: " + ", ".join(contract["allowed_actions"]),
            "Forbidden actions: " + ", ".join(contract["forbidden_actions"]),
            "Owner approval gates: " + " | ".join(contract["owner_approval_gates"]),
            "Acceptance criteria: " + " | ".join(contract["acceptance_criteria"]),
            "V4 publication may create one scoped feature-branch commit, push that exact branch, and open one pull request; merge, deployment, database, secrets and production remain forbidden.",
        ]
    )
    return submit.validate_text("instructions", text, submit.INSTRUCTION_LIMIT)


def render_task(
    project: dict[str, Any],
    *,
    task_id: str,
    number: int,
    contract: dict[str, Any],
    scope: list[str],
) -> str:
    required_commands = project.get("code_required_commands", ["git", "python3"])
    required_checks = project.get("code_required_checks", [])
    if not isinstance(required_commands, list) or not all(
        isinstance(command, str) and command for command in required_commands
    ):
        raise KolV4Error("invalid KÖL code_required_commands")
    if not isinstance(required_checks, list) or not all(
        isinstance(check, str) and check for check in required_checks
    ):
        raise KolV4Error("invalid KÖL code_required_checks")
    submit.validate_npm_run_checks(project, required_checks)

    values = [
        ("Task-ID", task_id),
        ("Execution-Mode", "code"),
        ("Operation-Profile", "none"),
        ("Project-Path", project["path"]),
        ("Base-Branch", project["base_branch"]),
        ("Work-Branch", gateway.work_branch(number)),
        ("Agent-Context", project["agent_context"]),
        ("Goal", contract["title"]),
        ("Instructions", render_instructions(number, contract)),
        ("Scope", "Only the approved Scope-Files listed below"),
        (
            "Out-of-Scope",
            "All files outside Scope-Files; merge, deployment, database mutation, secrets, payment activation, production change",
        ),
        ("Pass-Criteria", "Requested change is complete and all required checks pass"),
        ("Required-Checks", ", ".join(required_checks) if required_checks else "none"),
        ("Required-Commands", ", ".join(required_commands)),
        ("Required-Environment", "none"),
        ("Owner-Approval-Required", "yes"),
        ("Scope-Files", ", ".join(scope)),
        ("Publication-Contract-Version", str(VERSION)),
        ("Publication-Action", PUBLICATION_ACTION),
        ("Publication-Source-Issue", str(number)),
        ("Publication-Repository", REPOSITORY),
        ("Publication-Allowed-Actions", ", ".join(contract["allowed_actions"])),
        ("Publication-Forbidden-Actions", ", ".join(contract["forbidden_actions"])),
        ("Publication-Contract-Digest", contract["digest"]),
    ]
    return "\n".join(f"{key}: {value}" for key, value in values) + "\n"


def submit_contract(number: int, contract: dict[str, Any]) -> dict[str, str]:
    projects = submit.read_registry(gateway.ROOT)
    project = projects.get(PROJECT)
    if not isinstance(project, dict):
        raise KolV4Error("KÖL project registry entry is unavailable")
    if Path(project["path"]) != PROJECT_PATH:
        raise KolV4Error("KÖL project registry path differs from fixed V4 target")
    if project.get("base_branch") != "main":
        raise KolV4Error("KÖL V4 requires main as the base branch")
    if any(
        project.get(flag) is not False
        for flag in ("allow_commits", "allow_push", "allow_merge", "allow_deployment")
    ):
        raise KolV4Error("generic KÖL write authority must remain disabled")

    scope = submit.validate_scope(PROJECT_PATH, contract["scope"], project["allowed_scope"])
    task_id = submit.make_task_id(PROJECT)
    content = render_task(
        project,
        task_id=task_id,
        number=number,
        contract=contract,
        scope=scope,
    )
    destination = gateway.STATE_ROOT / "queue" / "pending" / f"{task_id}.md"
    submit.atomic_create(destination, content)
    return {"task_id": task_id, "queue": "pending"}


def parse_task_publication_metadata(task_text: str) -> dict[str, Any]:
    def field(name: str) -> str:
        return gateway.re.search(  # type: ignore[attr-defined]
            rf"(?mi)^\s*{gateway.re.escape(name)}:\s*(.*?)\s*$", task_text
        ).group(1).strip() if gateway.re.search(  # type: ignore[attr-defined]
            rf"(?mi)^\s*{gateway.re.escape(name)}:\s*(.*?)\s*$", task_text
        ) else ""

    version = field("Publication-Contract-Version")
    action = field("Publication-Action")
    source = field("Publication-Source-Issue")
    repository = field("Publication-Repository")
    digest = field("Publication-Contract-Digest")
    allowed = [item.strip() for item in field("Publication-Allowed-Actions").split(",") if item.strip()]
    forbidden = [item.strip() for item in field("Publication-Forbidden-Actions").split(",") if item.strip()]
    if version != str(VERSION) or action != PUBLICATION_ACTION or repository != REPOSITORY:
        raise KolV4Error("approved KÖL task lacks the exact V4 publication metadata")
    try:
        source_issue = int(source)
    except ValueError as exc:
        raise KolV4Error("approved KÖL task has invalid V4 source issue") from exc
    if source_issue <= 0 or len(digest) != 64:
        raise KolV4Error("approved KÖL task has incomplete V4 publication metadata")
    return {
        "source_issue": source_issue,
        "allowed_actions": allowed,
        "forbidden_actions": forbidden,
        "digest": digest,
    }
