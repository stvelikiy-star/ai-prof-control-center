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
import gateway_autonomous_intake as autonomous
import atomic_route_guard as route
import submit_task as submit


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
            task_id = existing["task_id"]
            state, _reason = gateway.task_public_state(task_id)
            if state == "not found":
                # Fail closed: the gateway remembered an imported task but its
                # queue artifact no longer exists. Never create a duplicate.
                if existing.get("status") == "orphaned":
                    return False
                gateway.post_comment(
                    number,
                    "AI PROF: нарушение целостности очереди. Ранее принятое "
                    f"задание {gateway.sanitize(task_id, 160)} отсутствует. "
                    "Автоматическое повторное создание запрещено. "
                    "Проверьте историю и восстановление очереди.",
                )
                existing["status"] = "orphaned"
                existing["last_reported_state"] = "not found"
                existing["last_reported_reason"] = ""
                return True
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
    except (gateway.GatewayError, submit.IntakeError) as exc:
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
        if autonomous.is_autonomous(issue):
            return autonomous.process(issue, issues_state)
        if kol_v4.is_v4_issue(issue):
            return process_kol_v4_issue(issue, issues_state)
        return prior_process_issue(issue, issues_state)

    gateway.process_issue = process_issue


def main() -> int:
    install_runtime_adapters()
    # Pin at process startup, not per poll: modifying source files while an
    # old Gateway runs cannot forge an attestation for new loaded code.
    pinned_code_digest = route.code_digest()
    prior_poll_once = gateway.poll_once

    def attested_poll_once() -> int:
        try:
            route.attest_gateway(start_digest=pinned_code_digest)
        except route.RouteError as exc:
            raise gateway.GatewayError('gateway route attestation unavailable') from exc
        result=prior_poll_once()
        # The base poll only visits open Issues; verify terminal records for
        # previously closed owner Issues without posting comments or replaying.
        state=gateway.load_state()
        if autonomous.reconcile_closed_terminal(state['issues']):
            gateway.save_state(state)
        return result

    gateway.poll_once = attested_poll_once
    return gateway.main()


if __name__ == "__main__":
    raise SystemExit(main())
