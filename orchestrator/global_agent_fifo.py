#!/usr/bin/env python3
"""Durable metadata-only FIFO for reviewed tasks, no arbitrary execution.

Integration adapters MUST verify owner authority and exact task contract before
submitting. This module does not spawn agents, run commands, or grant writes.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Callable, Iterator

import global_agent_admission as admission
import project_registry

ROOT = Path('/home/agent/.local/state/ai-prof-control-center/queue/global-agent-fifo-v1')
PROJECT_ROOT = admission.REPO_ROOT
QUEUES = ('pending', 'active', 'completed', 'blocked')
TASK_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
MAX_ENTRY_BYTES = 1024


class FIFOError(RuntimeError):
    pass


def project_check(project_id: str, root: Path) -> None:
    if not isinstance(project_id, str) or not TASK_ID.fullmatch(project_id):
        raise FIFOError('invalid project')
    try:
        entry = project_registry.load_projects(root).get(project_id)
        if entry is None or entry.get('enabled') is not True:
            raise FIFOError('project not explicitly enabled')
    except project_registry.ProjectPolicyError as exc:
        raise FIFOError('project registry unavailable') from exc


def safe_directory(path: Path) -> None:
    if path.is_symlink():
        raise FIFOError('unsafe queue directory')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise FIFOError('unsafe queue directory')


@contextmanager
def mutex(root: Path) -> Iterator[None]:
    safe_directory(root)
    path = root / '.mutex'
    if path.is_symlink():
        raise FIFOError('unsafe mutex')
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise FIFOError('unsafe mutex permissions')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)
    except OSError as exc:
        raise FIFOError('mutex unavailable') from exc


def records(root: Path, queue: str) -> list[Path]:
    if queue not in QUEUES:
        raise FIFOError('unknown queue')
    path = root / queue
    safe_directory(path)
    files = sorted(path.iterdir())
    if len(files) > 1000 or any(x.is_symlink() or not x.is_file()
                                or not re.fullmatch(r'[0-9]{12}-[0-9a-f]{16}\.json', x.name)
                                or x.stat().st_size > MAX_ENTRY_BYTES
                                for x in files):
        raise FIFOError('unsafe queue contents')
    return files


def read(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > MAX_ENTRY_BYTES:
        raise FIFOError('unsafe ticket')
    try:
        item = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise FIFOError('invalid ticket') from exc
    if (not isinstance(item, dict) or set(item) != {'version','sequence','project_id','task_id','contract_sha256'}
        or item.get('version') != 1 or type(item.get('sequence')) is not int
        or item['sequence'] < 1 or not isinstance(item.get('project_id'), str)
        or not isinstance(item.get('task_id'), str)
        or not TASK_ID.fullmatch(item['task_id'])
        or not isinstance(item.get('contract_sha256'), str)
        or not SHA.fullmatch(item['contract_sha256'])):
        raise FIFOError('ticket contract invalid')
    return item


def store(path: Path, item: dict) -> None:
    if path.exists() or path.is_symlink():
        raise FIFOError('ticket already exists')
    data = (json.dumps(item, sort_keys=True, separators=(',',':'))+'\n').encode()
    if len(data) > MAX_ENTRY_BYTES:
        raise FIFOError('ticket too large')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd,'wb') as writer:
            writer.write(data)
            writer.flush()
            os.fsync(writer.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def submit(project_id: str, task_id: str, contract_sha256: str, *,
           owner_verify: Callable[[str,str,str],bool], root: Path = ROOT,
           registry_root: Path = PROJECT_ROOT) -> tuple[str,str]:
    """Idempotently stage a verified task; no code execution occurs."""
    project_check(project_id, registry_root)
    if not isinstance(task_id,str) or not TASK_ID.fullmatch(task_id):
        raise FIFOError('invalid task identifier')
    if not isinstance(contract_sha256,str) or not SHA.fullmatch(contract_sha256):
        raise FIFOError('invalid immutable contract digest')
    if not callable(owner_verify) or owner_verify(project_id, task_id, contract_sha256) is not True:
        raise FIFOError('owner-review evidence not verified')
    with mutex(root):
        existing = [(queue,p,read(p)) for queue in QUEUES for p in records(root,queue)]
        for queue,path,item in existing:
            if item['project_id'] == project_id and item['task_id'] == task_id:
                if item['contract_sha256'] != contract_sha256:
                    raise FIFOError('immutable task contract changed')
                return queue,path.name
        maximum = max((item['sequence'] for _q,_p,item in existing),default=0)
        # Even after retention/cleanup, counter preserves monotonic order.
        counter = root / '.sequence'
        if counter.is_symlink():
            raise FIFOError('unsafe sequence counter')
        if counter.exists():
            raw = counter.read_text(encoding='ascii').strip()
            if not raw.isdecimal() or len(raw)>12:
                raise FIFOError('invalid sequence counter')
            maximum=max(maximum,int(raw))
        sequence=maximum+1
        if sequence>999999999999:
            raise FIFOError('queue exhausted')
        temp_fd,temp_file=tempfile.mkstemp(prefix='.sequence-',dir=root)
        try:
            with os.fdopen(temp_fd,'w') as file:
                file.write(str(sequence)+'\n')
                file.flush();os.fsync(file.fileno())
            os.chmod(temp_file,0o600)
            os.replace(temp_file,counter)
        finally:
            if os.path.exists(temp_file):os.unlink(temp_file)
        digest=hashlib.sha256((project_id+'\0'+task_id).encode()).hexdigest()[:16]
        name=f'{sequence:012d}-{digest}.json'
        entry={'version':1,'sequence':sequence,'project_id':project_id,
               'task_id':task_id,'contract_sha256':contract_sha256}
        store(root/'pending'/name,entry)
        return 'pending',name


@dataclass
class Claimed:
    item: dict
    name: str
    lease: admission.Lease

    def close(self) -> None:
        self.lease.close()


def claim(*, root: Path = ROOT, registry_root: Path = PROJECT_ROOT,
          lease_lock: Path = admission.DEFAULT_LOCK) -> Claimed | None:
    """Take the earliest reviewed ticket only if global execution is free.

    No task is launched by this function. Its caller must retain the lease and
    use its own trusted worker, with no replay after an interrupted execution.
    """
    with mutex(root):
        pending = records(root,'pending')
        active = records(root,'active')
        if active:
            raise FIFOError('active ticket requires explicit recovery')
        if not pending:
            return None
        path=pending[0]
        item=read(path)
        project_check(item['project_id'],registry_root)
        try:
            lease=admission.acquire(item['project_id'],registry_root=registry_root,lock_path=lease_lock)
        except admission.AdmissionError as exc:
            raise FIFOError('global execution lock unavailable') from exc
        if lease is None:
            return None
        try:
            target=root/'active'/path.name
            if target.exists() or target.is_symlink():
                raise FIFOError('duplicate active task')
            path.replace(target)
            return Claimed(item,path.name,lease)
        except BaseException:
            lease.close()
            raise


def finish(job: Claimed, *, remote_evidence_valid: bool, root: Path = ROOT) -> str:
    """Mark verified completed or blocked. Never auto-retry a claimed task."""
    try:
        with mutex(root):
            active = root/'active'/job.name
            if not active.is_file() or active.is_symlink() or read(active)!=job.item:
                raise FIFOError('active contract mismatch')
            queue='completed' if remote_evidence_valid is True else 'blocked'
            target=root/queue/job.name
            if target.exists() or target.is_symlink():
                raise FIFOError('duplicate terminal task')
            safe_directory(target.parent)
            active.replace(target)
            return queue
    finally:
        job.close()


def recover(*, root: Path = ROOT,
            registry_root: Path = PROJECT_ROOT,
            lease_lock: Path = admission.DEFAULT_LOCK) -> int:
    """Recover only when no cooperative worker owns global lease.

    Active tasks are blocked without re-execution; remote reconciliation must
    be performed separately with validated proof.
    """
    with mutex(root):
        active=records(root,'active')
        if not active:
            return 0
        project_id=read(active[0])['project_id']
        project_check(project_id,registry_root)
        lease=admission.acquire(project_id,registry_root=registry_root,lock_path=lease_lock)
        if lease is None:
            raise FIFOError('worker may still be running')
        with lease:
            for path in active:
                read(path)
                dest=root/'blocked'/path.name
                safe_directory(dest.parent)
                if dest.exists() or dest.is_symlink():
                    raise FIFOError('duplicate blocked task')
                path.replace(dest)
            return len(active)
