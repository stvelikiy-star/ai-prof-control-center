#!/usr/bin/env python3
"""Atomic, non-privileged route freeze for the owner-only autonomous lane.

Do not change this module into a generic command runner. The only executable
route supported today is legacy; frozen blocks new intake and legacy starts.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Iterator

import github_task_gateway as gateway
import global_agent_admission as admission

RUN_ROOT = gateway.STATE_ROOT / 'run'
ROUTE_LOCK = RUN_ROOT / 'autonomous-route-v1.lock'
MODE_FILE = RUN_ROOT / 'autonomous-route-v1.json'
ATTESTATION_FILE = RUN_ROOT / 'autonomous-route-gateway-attestation-v1.json'
ROUTE_PROTOCOL = 'ai-prof-owner-fifo-v1'
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
        if payload not in ({'version':1,'mode':'legacy'}, {'version':1,'mode':'frozen'},
                           {'version':1,'mode':'fifo'}):
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
    if new_mode not in ('legacy','frozen','fifo'):
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


# A live Gateway must attest the code loaded when the process started.
# The read-only module digest includes both route users and their executor.
_ATTESTED_SOURCES = (
    'atomic_route_guard.py', 'gateway_autonomous_intake.py',
    'github_task_gateway_service_v4.py', 'autonomous_shared_queue_worker.py',
    'global_fifo_owner_runtime.py', 'global_fifo_owner_adapter.py',
    'global_agent_fifo.py', 'global_agent_admission.py',
)


def code_digest() -> str:
    root=Path(__file__).resolve().parent
    digest=hashlib.sha256()
    try:
        for filename in _ATTESTED_SOURCES:
            path=root/filename
            if path.is_symlink() or not path.is_file():
                raise RouteError('unsafe code attestation source')
            digest.update(filename.encode('ascii')+b'\0')
            digest.update(path.read_bytes())
        return digest.hexdigest()
    except OSError as exc:
        raise RouteError('code attestation unavailable') from exc


def _start_ticks(pid: int) -> int:
    try:
        raw=(Path('/proc')/str(pid)/'stat').read_text(encoding='ascii')
        return int(raw.rsplit(')',1)[1].split()[19])
    except (OSError,ValueError,IndexError) as exc:
        raise RouteError('gateway process start cannot be verified') from exc


def attest_gateway(*, start_digest: str) -> None:
    """Call from the owner-Gateway's poll loop while holding its main lock."""
    if not isinstance(start_digest,str) or len(start_digest)!=64:
        raise RouteError('invalid pinned process digest')
    payload={
        'version':1,'protocol':ROUTE_PROTOCOL,'pid':os.getpid(),
        'start_ticks':_start_ticks(os.getpid()),
        'code_sha256':start_digest,'updated_unix':int(time.time()),
    }
    path=ATTESTATION_FILE
    _directory(path.parent)
    if path.is_symlink():raise RouteError('unsafe gateway attestation path')
    fd,temp=tempfile.mkstemp(prefix='.route-gateway-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='ascii') as output:
            json.dump(payload,output,sort_keys=True)
            output.write('\n');output.flush();os.fsync(output.fileno())
        os.chmod(temp,0o600)
        os.replace(temp,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def verify_gateway_attested(*, file: Path | None = None,
                           now: int | None = None) -> bool:
    path=ATTESTATION_FILE if file is None else file
    if path.is_symlink() or not path.is_file():return False
    try:
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077 or info.st_size>512:
            return False
        data=json.loads(path.read_text(encoding='ascii'))
        if not isinstance(data,dict) or set(data)!={'version','protocol','pid','start_ticks','code_sha256','updated_unix'}:
            return False
        if data['version']!=1 or data['protocol']!=ROUTE_PROTOCOL:
            return False
        if type(data['pid']) is not int or type(data['start_ticks']) is not int or type(data['updated_unix']) is not int:
            return False
        stamp=int(time.time()) if now is None else now
        if not 0 <= stamp-data['updated_unix'] <= 180:
            return False
        if _start_ticks(data['pid']) != data['start_ticks']:
            return False
        return data['code_sha256']==code_digest()
    except (OSError,ValueError,UnicodeError,RouteError,KeyError,TypeError):
        return False


def transition(target: str, *, lock: Path | None = None,
               file: Path | None = None, worker_lock: Path | None = None,
               legacy: Path | None = None, fifo: Path | None = None,
               global_lock: Path | None = None,
               registry_root: Path | None = None,
               gateway_attestation: Path | None = None) -> str:
    """Atomic legacy<->frozen only. Caller must ensure updated services run.

    Never activates FIFO, changes tasks, executes work or restarts services.
    """
    if target not in ('legacy','frozen','fifo'):
        raise RouteError('unsupported route mode')
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
                    proof = (ATTESTATION_FILE if gateway_attestation is None
                             else gateway_attestation)
                    if not verify_gateway_attested(file=proof):
                        raise RouteError('updated owner Gateway process has not attested')
                    # Route activation and rollback require an explicit frozen
                    # intermediary, never a direct executable-to-executable hop.
                    if {current,target} == {'legacy','fifo'}:
                        raise RouteError('direct executable route switch forbidden')
                    if not all(_empty(path) for path in (
                        old/'autonomous_pending',old/'autonomous_active',
                        new/'pending',new/'active')):
                        raise RouteError('pending or active tasks prevent route switch')
                    _write(state,target)
                    return target
        finally:os.close(worker_fd)
    except OSError as exc:
        raise RouteError('cannot acquire existing worker lock') from exc
