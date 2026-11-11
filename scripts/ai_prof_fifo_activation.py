#!/usr/bin/env python3
"""Explicit owner-only AI PROF FIFO route preflight/activation/rollback.

Default is read-only. No auto-run, no sudo, no arbitrary command execution,
no customer project access, and no automatic PR merge or deployment.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'orchestrator'))
import atomic_route_guard as route
import global_fifo_cutover_review as review
import sequential_job_dispatcher as baseline
import autonomous_shared_queue_worker as worker

CONFIRM='AI_PROF_OWNER_FIFO_ONLY'


class ActivationError(RuntimeError):
    pass


def revision() -> str:
    try:
        result=subprocess.run(['git','-C',str(ROOT),'rev-parse','HEAD'],
                              capture_output=True,text=True,timeout=10,check=True)
        sha=result.stdout.strip()
        if len(sha)!=40 or any(char not in '0123456789abcdef' for char in sha):
            raise ActivationError('unable to verify local revision')
        return sha
    except (OSError,subprocess.SubprocessError) as exc:
        raise ActivationError('local revision unavailable') from exc


def services_active() -> bool:
    # Only read systemd states. No service restart or privileged command.
    try:
        commands=(['systemctl','is-active','--quiet','ai-prof-github-task-gateway.service'],
                  ['systemctl','--user','is-active','--quiet','ai-prof-autonomous-shared-queue.timer'])
        return all(subprocess.run(cmd,check=False,timeout=10).returncode==0
                   for cmd in commands)
    except (OSError,subprocess.SubprocessError):
        return False


def audit() -> dict:
    """Fail-closed, read-only current host acceptance evidence."""
    try:
        mode=route.mode()
        attested=route.verify_gateway_attested()
        cutover=review.review()
        resources,reason=baseline.resource_gate()
        codex,code_reason=worker.safe_codex_launch_gate()
        active=services_active()
        current=revision()
    except (route.RouteError,review.CutoverError,ActivationError,
            OSError,ValueError) as exc:
        return {'ready':False,'reason':'AUDIT_UNVERIFIED','error_type':type(exc).__name__}
    return {
        'ready':bool(mode=='legacy' and attested and cutover.get('ready') is True
                     and resources and codex and active),
        'route':mode,'gateway_attested':attested,
        'cutover':cutover,'resource_gate':reason,'resource_pass':resources,
        'external_agent_gate':code_reason,'external_agents_pass':codex,
        'services_active':active,'revision':current,
    }


def activate(*, expected_sha: str, confirmation: str) -> str:
    if confirmation!=CONFIRM:
        raise ActivationError('explicit owner confirmation required')
    result=audit()
    if not result.get('ready') or result.get('revision')!=expected_sha:
        raise ActivationError('all host gates and exact SHA must pass')
    route.transition('frozen')
    try:
        # A frozen Gateway/worker cannot create or start a new task while the
        # second transition reacquires both locks and rechecks both queues.
        route.transition('fifo')
    except BaseException:
        try:route.transition('legacy')
        except (route.RouteError,OSError):
            # Leave safely frozen rather than allowing an unverified route.
            pass
        raise
    if route.mode()!='fifo':
        raise ActivationError('FIFO mode not independently confirmed')
    return 'FIFO_OWNER_ROUTE_ACTIVE'


def rollback(*, expected_sha: str, confirmation: str) -> str:
    if confirmation!=CONFIRM or revision()!=expected_sha:
        raise ActivationError('exact SHA and explicit owner confirmation required')
    if not route.verify_gateway_attested():
        raise ActivationError('live Gateway attestation not verified')
    if route.mode()=='fifo':
        route.transition('frozen')
    elif route.mode()!='frozen':
        raise ActivationError('route is neither fifo nor frozen')
    route.transition('legacy')
    return 'LEGACY_OWNER_ROUTE_RESTORED'


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--activate',action='store_true')
    action.add_argument('--rollback',action='store_true')
    parser.add_argument('--expected-sha',default='')
    parser.add_argument('--confirm',default='')
    args=parser.parse_args(argv)
    try:
        if args.activate:
            print(activate(expected_sha=args.expected_sha,confirmation=args.confirm))
        elif args.rollback:
            print(rollback(expected_sha=args.expected_sha,confirmation=args.confirm))
        else:
            print(json.dumps(audit(),ensure_ascii=False,sort_keys=True))
        return 0
    except (ActivationError,route.RouteError,review.CutoverError,OSError) as exc:
        print('AI_PROF_FIFO_ACTIVATION_BLOCKED '+type(exc).__name__,file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
