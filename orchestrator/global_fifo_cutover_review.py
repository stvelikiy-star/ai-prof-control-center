#!/usr/bin/env python3
"""No-side-effect acceptance probe for a *future* global FIFO handoff.

Never flips a switch, migrates a task or executes an agent. Locks are held
only during the inspection and must NOT be taken as future authorization.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat

import autonomous_shared_queue_worker as worker
import gateway_autonomous_intake as intake
import global_agent_admission as admission
import global_agent_fifo as fifo


class CutoverError(RuntimeError):
    pass


def safe_count(folder: Path, *, allow_missing: bool = False) -> int:
    if folder.is_symlink():
        raise CutoverError('unsafe queue directory')
    if not folder.exists() and allow_missing:
        return 0
    if not folder.is_dir():
        raise CutoverError('queue directory unavailable')
    count = 0
    try:
        for item in folder.iterdir():
            count += 1
            if count > 1000 or item.is_symlink() or not item.is_file() or item.suffix != '.json':
                raise CutoverError('unsafe queue entry')
    except OSError as exc:
        raise CutoverError('cannot inspect queue directory') from exc
    return count


def review(*, legacy_root: Path = intake.QUEUE_ROOT,
           fifo_root: Path = fifo.ROOT,
           legacy_lock: Path = worker.RUN_LOCK,
           global_lock: Path = admission.DEFAULT_LOCK,
           registry_root: Path = admission.REPO_ROOT) -> dict[str, object]:
    """Atomic *snapshot* only: inspect both queue roots under both locks.

    The lock is intentionally not held beyond this call. Therefore PASS means
    'quiescent at inspection time', NOT permission to enable migration later.
    """
    if legacy_lock.is_symlink() or not legacy_lock.is_file():
        raise CutoverError('legacy lock missing or unsafe')
    try:
        fd = os.open(legacy_lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            status = os.fstat(fd)
            if (not stat.S_ISREG(status.st_mode) or status.st_uid != os.getuid()
                or status.st_mode & 0o077):
                raise CutoverError('unsafe legacy lock permissions')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'ready': False, 'reason': 'LEGACY_WORKER_BUSY'}
            try:
                lease = admission.acquire('ai-prof-control-center',
                                          registry_root=registry_root,
                                          lock_path=global_lock)
            except admission.AdmissionError as exc:
                raise CutoverError('global admission unavailable') from exc
            if lease is None:
                return {'ready': False, 'reason': 'GLOBAL_AGENT_BUSY'}
            with lease:
                if legacy_root.is_symlink() or fifo_root.is_symlink():
                    raise CutoverError('unsafe queue root')
                legacy_pending = safe_count(legacy_root/'autonomous_pending')
                legacy_active = safe_count(legacy_root/'autonomous_active')
                fifo_pending = safe_count(fifo_root/'pending',allow_missing=True)
                fifo_active = safe_count(fifo_root/'active',allow_missing=True)
                if legacy_active or fifo_active:
                    return {'ready': False, 'reason': 'ACTIVE_TASK_REQUIRES_RECOVERY'}
                if legacy_pending or fifo_pending:
                    return {'ready': False, 'reason': 'PENDING_TASK_REQUIRES_HANDOFF'}
                return {'ready': True, 'reason': 'QUIESCENT_SNAPSHOT_ONLY'}
        finally:
            os.close(fd)
    except (OSError, ValueError) as exc:
        raise CutoverError('cutover review cannot verify locks') from exc
