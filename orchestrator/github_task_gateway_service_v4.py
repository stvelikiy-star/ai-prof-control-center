#!/usr/bin/env python3
"""Runtime gateway entrypoint with one bounded KÖL V4 publication contract.

All established V1-V3 task handling, AK BERMET preparation and KÖL recovery
routes remain delegated to github_task_gateway_service. This adapter intercepts
only the distinct `[AI-PROF-KOL-TASK]` V4 surface.
"""
from __future__ import annotations

import github_task_gateway as gateway
import github_task_gateway_service as established
import kol_publication_contract_v4 as kol_v4


def _mark_v4_record(record: dict) -> dict:
    record["kind"] = kol_v4.RECORD_KIND
    return record


def process_kol_v4_issue(issue: dict, issues_state: dict) -> bool:
    number = gateway.issue_number(issue)
    key = str(number)
    existing = issues_state.get(key)
    if isinstance(existing, dict):
        if existing.get("task_id"):
            _mark_v4_record(existing)
            return gateway.report_task_state(number, existing)
        return False

    try:
        contract = kol_v4.parse_contract(issue)
        recovered = gateway.find_existing_task_for_issue(number)
        if recovered:
            record = gateway._record_import(issues_state, number, recovered)
            _mark_v4_record(record)
            gateway.post_comment(
                number,
                "AI PROF KÖL V4 recovered the existing task after state reconciliation.\n"
                f"Task-ID: {recovered['task_id']}\nQueue: {recovered['queue']}\n"
                "No duplicate task was created.",
            )
            return True
        created = kol_v4.submit_contract(number, contract)
    except gateway.GatewayError as exc:
        code = gateway.sanitize(exc, 500)
        issues_state[key] = {
            "status": "rejected",
            "kind": kol_v4.RECORD_KIND,
            "code": code,
        }
        gateway.post_comment(
            number,
            "AI PROF KÖL V4 rejected: "
            + gateway.sanitize(exc, 1000)
            + ". No task was enqueued.",
        )
        return True

    record = gateway._record_import(issues_state, number, created)
    _mark_v4_record(record)
    gateway.post_comment(
        number,
        "AI PROF KÖL V4 task imported\n"
        f"Task-ID: {created['task_id']}\nQueue: {created['queue']}\n"
        "Publication authority: one scoped feature-branch commit + exact branch push + pull request only.\n"
        "Merge, deployment, database/Supabase mutation, payments, secrets and production changes remain forbidden.",
    )
    return True


def install_runtime_adapters() -> None:
    established.install_runtime_adapters()
    prior_process_issue = gateway.process_issue

    def process_issue(issue: dict, issues_state: dict) -> bool:
        if kol_v4.is_v4_issue(issue):
            return process_kol_v4_issue(issue, issues_state)
        return prior_process_issue(issue, issues_state)

    gateway.process_issue = process_issue


def main() -> int:
    install_runtime_adapters()
    return gateway.main()


if __name__ == "__main__":
    raise SystemExit(main())
