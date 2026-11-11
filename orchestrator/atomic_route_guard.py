#!/usr/bin/env python3
"""Atomic, non-privileged route freeze for the owner-only autonomous lane.

Do not change this module into a generic command runner. The only executable
route supported today is legacy; frozen blocks new intake and legacy starts.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Iterator

import github_task_gateway as gateway
import global_agent_admission as admission

RUN_ROOT = gateway.STATE_ROOT / 'run'
ROUTE_LOCK = RUN_ROOT / 'autonomous-route-v1.lock'
MODE_FILE = RUN_ROOT / 'autonomous-route-v1.json'
LEGACY_WORKER_LOCK = RUN_ROOT / 'autonomous-shared-queue.lock'
LEGACY_QUEUE = gateway.STATE_ROOT / 'queue'
FIFO_QUEUE = LEGACY_QUEUE / 'global-agent-fifo-v1'


class RouteError(RuntimeError):
    pass


def _directory(path: Path) -> None:
    if path.is_symlink():
        raise RouteError('unsafe route directory')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    s=path.stat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode & 0o077:
        raise RouteError('insecure route directory')


def _fd(path: Path, *, create: bool) -> int:
    if path.is_symlink():
        raise RouteError('route lock symlink')
    flags=os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT
    f=os.open(path,flags,0o600)
    s=os.fstat(f)
    if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode & 0o077:
        os.close(f)
        raise RouteError('unsafe route lock')
    return f


@contextmanager
def gate(*, lock: Path | None = None) -> Iterator[None]:
    path=ROUTE_LOCK if lock is None else lock
    try:
        _directory(path.parent)
        fd=_fd(path,create=True)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)
    except OSError as exc:
        raise RouteError('route lock unavailable') from exc


def mode(*, file: Path | None = None) -> str:
    path=MODE_FILE if file is None else file
    if path.is_symlink():
        raise RouteError('route mode symlink')
    if not path.exists():
        return 'legacy'  # Old releases have no flag and use only legacy.
    try:
        s=path.stat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode & 0o077 or s.st_size>128:
            raise RouteError('route mode file unsafe')
        payload=json.loads(path.read_text(encoding='ascii'))
        if payload not in ({'version':1,'mode':'legacy'}, {'version':1,'mode':'frozen'}):
            raise RouteError('unsupported route mode')
        return payload['mode']
    except (OSError,ValueError,UnicodeError) as exc:
        raise RouteError('invalid route mode') from exc


def permitted(*, file: Path | None = None) -> bool:
    """MUST be called while holding gate(). Unknown modes fail closed."""
    return mode(file=file)=='legacy'


def _empty(path: Path) -> bool:
    if path.is_symlink():
        raise RouteError('unsafe queue directory')
    if not path.exists():
        return True
    if not path.is_dir():
        raise RouteError('queue path is not a directory')
    try:
        for entry in path.iterdir():
            if entry.is_symlink() or not entry.is_file() or entry.suffix!='.json':
                raise RouteError('unsafe queue entry')
            return False
    except OSError as exc:
        raise RouteError('queue unreadable') from exc
    return True


def _write(path: Path, new_mode: str) -> None:
    if new_mode not in ('legacy','frozen'):
        raise RouteError('unsupported mode transition')
    _directory(path.parent)
    if path.is_symlink():
        raise RouteError('route mode symlink')
    payload=(json.dumps({'version':1,'mode':new_mode},sort_keys=True)+'\n').encode()
    f,tmp=tempfile.mkstemp(prefix='.route-',dir=path.parent)
    try:
        with os.fdopen(f,'wb') as handle:
            handle.write(payload);handle.flush();os.fsync(handle.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def transition(target: str, *, lock: Path | None = None,
               file: Path | None = None, worker_lock: Path | None = None,
               legacy: Path | None = None, fifo: Path | None = None,
               global_lock: Path | None = None,
               registry_root: Path | None = None) -> str:
    """Atomic legacy<->frozen only. Caller must ensure updated services run.

    Never activates FIFO, changes tasks, executes work or restarts services.
    """
    if target not in ('legacy','frozen'):
        raise RouteError('no FIFO activation route implemented')
    route_lock=ROUTE_LOCK if lock is None else lock
    state=MODE_FILE if file is None else file
    worker=LEGACY_WORKER_LOCK if worker_lock is None else worker_lock
    old=LEGACY_QUEUE if legacy is None else legacy
    new=FIFO_QUEUE if fifo is None else fifo
    if worker.is_symlink() or not worker.is_file():
        raise RouteError('legacy worker lock absent or unsafe')
    try:
        worker_fd=_fd(worker,create=False)
        try:
            try:fcntl.flock(worker_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RouteError('legacy worker busy')
            try:
                lease=admission.acquire(
                    'ai-prof-control-center',
                    registry_root=admission.REPO_ROOT if registry_root is None else registry_root,
                    lock_path=admission.DEFAULT_LOCK if global_lock is None else global_lock,
                )
            except admission.AdmissionError as exc:
                raise RouteError('global admission unavailable') from exc
            if lease is None:
                raise RouteError('another agent holds the execution lease')
            with lease:
                with gate(lock=route_lock):
                    current=mode(file=state)
                    if current==target:return current
                    if not all(_empty(path) for path in (
                        old/'autonomous_pending',old/'autonomous_active',
                        new/'pending',new/'active')):
                        raise RouteError('pending or active tasks prevent route switch')
                    _write(state,target)
                    return target
        finally:os.close(worker_fd)
    except OSError as exc:
        raise RouteError('cannot acquire existing worker lock') from exc
