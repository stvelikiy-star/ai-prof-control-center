#!/usr/bin/env python3
"""Fail-closed staging bridge: immutable owner GitHub issue -> isolated FIFO.

No real enqueue or execution is enabled in this phase. Historical tasks that
left the primary pending queue can never be silently replayed through FIFO.
"""
from __future__ import annotations

from pathlib import Path
import re

import autonomous_shared_queue_worker as worker
import gateway_autonomous_intake as intake
import global_agent_fifo as fifo
import github_task_gateway as gateway

PROJECT = 'ai-prof-control-center'
TASK = re.compile(r'AI_PROF_AUTO_MAIN_([1-9][0-9]{0,9})\Z')


class OwnerBridgeError(RuntimeError):
    pass


def pending_contract(task_id: str, *, legacy_root: Path = intake.QUEUE_ROOT) -> dict:
    """Re-fetch both owner and pilot issues, validate exact approved profile.

    Only an existing, non-symlink immutable pending record is admissible.
    Completed/blocked/active tasks are terminal/in-progress and must NOT be
    migrated into new runnable work.
    """
    if not isinstance(task_id, str) or (match := TASK.fullmatch(task_id)) is None:
        raise OwnerBridgeError('invalid authorized task ID')
    number = int(match.group(1))
    if number <= 0:
        raise OwnerBridgeError('invalid authorized issue')
    try:
        folders = ('autonomous_pending', 'autonomous_active',
                   'autonomous_completed', 'autonomous_blocked')
        locations = []
        for folder in folders:
            parent = legacy_root / folder
            if legacy_root.is_symlink() or parent.is_symlink():
                raise OwnerBridgeError('unsafe legacy queue directory')
            candidate = parent / (task_id + '.json')
            if candidate.is_symlink():
                raise OwnerBridgeError('unsafe legacy queue ticket')
            if candidate.exists():
                locations.append((folder, candidate))
        if len(locations) != 1 or locations[0][0] != 'autonomous_pending':
            raise OwnerBridgeError('task not uniquely pending')
        _, path = locations[0]
        data = worker.check_job(path)
        if (data.get('main_issue') != number
                or data.get('kind') != intake.RECORD_MARKER
                or data.get('deadline_seconds') != 14400):
            raise OwnerBridgeError('immutable task contract mismatch')
        return data
    except (OSError, ValueError, TypeError, worker.ConsumerError,
            gateway.GatewayError, intake.AutoIntakeError) as exc:
        raise OwnerBridgeError('owner/pilot contract not independently verified') from exc


def stage_isolated(task_id: str, *, legacy_root: Path,
                   fifo_root: Path, registry_root: Path = fifo.PROJECT_ROOT) -> tuple[str, str]:
    """Dry-run staging to a dedicated test FIFO, never the live runtime.

    The input is only the immutable task ID. No ability to select a new repo,
    operation, shell command, profile or change project access is exposed.
    """
    if fifo_root.resolve(strict=False) == fifo.ROOT.resolve(strict=False):
        raise OwnerBridgeError('live cross-project FIFO admission not authorized')
    if legacy_root.resolve(strict=False) == intake.QUEUE_ROOT.resolve(strict=False):
        raise OwnerBridgeError('live legacy queue migration not authorized')
    data = pending_contract(task_id, legacy_root=legacy_root)
    owner_digest = data['owner_issue_sha256']

    def verify(project: str, requested_task: str, sha: str) -> bool:
        if project != PROJECT or requested_task != task_id or sha != owner_digest:
            return False
        latest = pending_contract(requested_task, legacy_root=legacy_root)
        return latest == data

    return fifo.submit(PROJECT, task_id, owner_digest,
                       owner_verify=verify, root=fifo_root,
                       registry_root=registry_root)


def recheck_before_execution(ticket: dict, *, legacy_root: Path) -> dict:
    """Execution adapters must repeat independent GitHub proof at claim time."""
    if not isinstance(ticket, dict) or ticket.get('project_id') != PROJECT:
        raise OwnerBridgeError('wrong project')
    task_id = ticket.get('task_id')
    data = pending_contract(task_id, legacy_root=legacy_root)
    if ticket.get('contract_sha256') != data['owner_issue_sha256']:
        raise OwnerBridgeError('claimed digest differs from owner contract')
    return data
