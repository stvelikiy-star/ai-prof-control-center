#!/usr/bin/env python3
"""Bounded, owner-only FIFO runtime using existing approved AI PROF pilot.

Called only by the route-aware Gateway and the existing single-flight worker.
No arbitrary project, operation, shell command, merge or deployment authority.
"""
from __future__ import annotations

from pathlib import Path

import autonomous_shared_queue_worker as worker
import gateway_autonomous_intake as intake
import github_task_gateway as gateway
import global_agent_admission as admission
import global_agent_fifo as fifo
import global_fifo_owner_adapter as adapter

PROJECT='ai-prof-control-center'


class RuntimeErrorSafe(RuntimeError):
    pass


def _task_id(issue: dict) -> str:
    return f"AI_PROF_AUTO_MAIN_{gateway.issue_number(issue)}"


def _stage(task_id: str) -> tuple[str, str]:
    """Re-fetch exact GitHub owner and pilot Issue proof before FIFO submit."""
    data=adapter.pending_contract(task_id,legacy_root=intake.QUEUE_ROOT)
    digest=data['owner_issue_sha256']

    def verify(project: str,task: str,sha: str) -> bool:
        if project!=PROJECT or task!=task_id or sha!=digest:
            return False
        fresh=adapter.pending_contract(task,legacy_root=intake.QUEUE_ROOT)
        return fresh==data

    return fifo.submit(PROJECT,task_id,digest,owner_verify=verify,
                       root=fifo.ROOT,registry_root=admission.REPO_ROOT)


def process_issue(issue: dict, issues_state: dict) -> bool:
    """Route gate held by gateway_autonomous_intake.process().

    Write legacy owner-contract evidence first, then a FIFO ordering ticket.
    If interrupted between these steps, the legacy worker is disabled in FIFO
    mode and the next poll safely restages the *same* ticket, never a new job.
    """
    number=gateway.issue_number(issue)
    key=str(number)
    existing=issues_state.get(key)
    if existing is not None and not isinstance(existing,dict):
        raise RuntimeErrorSafe('invalid gateway issue state')
    if existing and existing.get('status') not in (
            'autonomous_queued','autonomous_fifo_queued','autonomous_fifo_waiting'):
        return intake.report_existing(issue,existing)
    if not existing:
        changed=intake._process_when_authorized_route(issue,issues_state)
        record=issues_state.get(key)
        if not isinstance(record,dict) or record.get('status')!='autonomous_queued':
            return changed
    record=issues_state[key]
    # The worker writes terminal ledger entries independently of the Gateway.
    # Reconcile those first, before attempting to re-stage a pending ticket;
    # otherwise a completed task remains permanently fifo_queued/waiting.
    if record.get('status') in ('autonomous_queued','autonomous_fifo_queued',
                                'autonomous_fifo_waiting'):
        locations=intake.locate(number)
        if len(locations)>1:
            raise RuntimeErrorSafe('ambiguous legacy terminal records')
        if len(locations)==1 and locations[0].parent.name in (
                'autonomous_completed','autonomous_blocked'):
            return intake.report_existing(issue,record)
    task_id=_task_id(issue)
    try:
        _stage(task_id)
    except (adapter.OwnerBridgeError, fifo.FIFOError, gateway.GatewayError,
            OSError,ValueError):
        # Never report a staged job until both durable records are present.
        # Do not mark terminal: retry only the review/stage operation on poll.
        if record.get('status')=='autonomous_fifo_waiting':
            return False
        record['status']='autonomous_fifo_waiting'
        return True
    if record.get('status')!='autonomous_fifo_queued':
        record['status']='autonomous_fifo_queued'
        return True
    return False


def _legacy_locations(ticket: dict) -> list[Path]:
    task=ticket['task_id']
    if not isinstance(task,str) or not adapter.TASK.fullmatch(task):
        raise RuntimeErrorSafe('invalid immutable FIFO task id')
    number=int(task.removeprefix('AI_PROF_AUTO_MAIN_'))
    paths=[]
    for state in intake.QUEUE_NAMES:
        path=intake.queue_path(state,number)
        if path.is_symlink():
            raise RuntimeErrorSafe('legacy ticket symlink')
        if path.exists():
            paths.append(path)
    if len(paths)>1:
        raise RuntimeErrorSafe('ambiguous legacy task records')
    return paths


def _terminalize_legacy(ticket: dict, *, completed: bool) -> None:
    records=_legacy_locations(ticket)
    if not records:
        return  # Missing record is anomalous; FIFO will block, never replay.
    path=records[0]
    target='autonomous_completed' if completed else 'autonomous_blocked'
    if path.parent.name==target:
        return
    # Never undo a terminal decision. Conflicting statuses require review.
    if path.parent.name in ('autonomous_completed','autonomous_blocked'):
        raise RuntimeErrorSafe('conflicting immutable legacy terminal decision')
    worker.relocate(path,target)


def _recover_active() -> str:
    active=fifo.records(fifo.ROOT,'active')
    if not active:
        return 'FIFO_IDLE'
    if len(active)!=1:
        raise RuntimeErrorSafe('ambiguous active FIFO tickets')
    path=active[0]
    ticket=fifo.read(path)
    if ticket['project_id']!=PROJECT:
        return 'FIFO_HEAD_UNSUPPORTED_PROJECT'
    lease=admission.acquire(PROJECT,registry_root=admission.REPO_ROOT,
                            lock_path=admission.DEFAULT_LOCK)
    if lease is None:
        return 'RESOURCE_DEFERRED_ORPHANED_WORKER_MAY_BE_ACTIVE'
    job=fifo.Claimed(ticket,path.name,lease)
    completed=False
    try:
        items=_legacy_locations(ticket)
        if len(items)==1 and items[0].parent.name!='autonomous_blocked':
            try:
                data=worker.check_job(items[0])
                if data['owner_issue_sha256']==ticket['contract_sha256']:
                    completed=worker.verify_published(data)
            except (worker.ConsumerError,gateway.GatewayError,OSError,ValueError):
                pass
        try:
            _terminalize_legacy(ticket,completed=completed)
        except (RuntimeErrorSafe,worker.ConsumerError,OSError):
            completed=False
        outcome=fifo.finish(job,remote_evidence_valid=completed,root=fifo.ROOT)
        return 'FIFO_RECOVERED_REMOTE_PROOF_PASS' if outcome=='completed' else 'FIFO_INTERRUPTED_BLOCKED'
    finally:
        job.close()


def tick() -> str:
    """Invoked under the existing legacy RUN_LOCK by worker.main().

    FIFO mode is explicitly required; no second systemd executor is started.
    """
    import atomic_route_guard as route
    if route.mode()!='fifo':
        return 'RESOURCE_DEFERRED_FIFO_ROUTE_NOT_ACTIVE'
    active=fifo.records(fifo.ROOT,'active')
    if active:
        return _recover_active()  # No reexecution after process crash.
    # If a legacy active record survived a prior filesystem/remote error,
    # fail closed rather than advancing a later FIFO ticket.
    legacy_active=intake.QUEUE_ROOT/'autonomous_active'
    if legacy_active.is_symlink():
        raise RuntimeErrorSafe('unsafe legacy active directory')
    if legacy_active.exists() and any(legacy_active.iterdir()):
        return 'FIFO_LEGACY_ORPHAN_REQUIRES_REVIEW'
    pending=fifo.records(fifo.ROOT,'pending')
    if not pending:
        return 'IDLE'
    head=fifo.read(pending[0])
    if head.get('project_id')!=PROJECT:
        return 'FIFO_HEAD_UNSUPPORTED_PROJECT' # no jumping ahead
    import sequential_job_dispatcher as baseline
    okay,reason=baseline.resource_gate()
    if not okay:return 'RESOURCE_DEFERRED_'+reason
    okay,reason=worker.safe_codex_launch_gate()
    if not okay:return 'RESOURCE_DEFERRED_'+reason
    job=fifo.claim(root=fifo.ROOT,registry_root=admission.REPO_ROOT,
                   lease_lock=admission.DEFAULT_LOCK)
    if job is None:return 'RESOURCE_DEFERRED_GLOBAL_AGENT_BUSY'
    completed=False
    try:
        data=adapter.recheck_before_execution(job.item,legacy_root=intake.QUEUE_ROOT)
        path=intake.queue_path('autonomous_pending',data['main_issue'])
        active_path=worker.relocate(path,'autonomous_active')
        # Global lease is inherited by subprocess for the entire Codex task.
        result=worker.run_pilot_process(data,lease_fd=job.lease.fd)
        completed=result==0 and worker.verify_published(data)
        _terminalize_legacy(job.item,completed=completed)
    except (adapter.OwnerBridgeError,worker.ConsumerError,gateway.GatewayError,
            RuntimeErrorSafe,OSError,ValueError):
        completed=False
        try:_terminalize_legacy(job.item,completed=False)
        except (RuntimeErrorSafe,worker.ConsumerError,OSError):pass
    finally:
        # A failure in the remote/legacy publication path leaves the FIFO
        # active for proof-only reconciliation, NEVER a second worker launch.
        outcome=fifo.finish(job,remote_evidence_valid=completed,root=fifo.ROOT)
    return 'FIFO_OWNER_COMPLETED_PR_TELEGRAM_PASS' if outcome=='completed' else 'FIFO_OWNER_WORKER_BLOCKED'
