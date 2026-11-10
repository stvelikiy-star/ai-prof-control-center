#!/usr/bin/env python3
"""Cooperative cross-project single-flight admission, no command execution.

All participating agent runners use the same Linux advisory flock. A caller
must already have its own immutable, owner-reviewed task contract; this module
only checks enabled project registration and reserves the global agent slot.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import stat
from dataclasses import dataclass

import project_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = Path('/home/agent/.local/state/ai-prof-control-center/run/global-agent-exec-v1.lock')


class AdmissionError(RuntimeError):
    pass


@dataclass
class Lease:
    fd: int
    project_id: str
    closed: bool = False

    def close(self) -> None:
        if not self.closed:
            # The parent owns the lease while its worker runs. The worker may
            # inherit the fd, preserving the lock if the parent exits first.
            self.closed = True
            os.close(self.fd)

    def __enter__(self) -> 'Lease':
        return self

    def __exit__(self, *_errors: object) -> None:
        self.close()


def acquire(project_id: str, *, registry_root: Path = REPO_ROOT,
            lock_path: Path = DEFAULT_LOCK) -> Lease | None:
    """Return a held non-blocking lease, None if busy, or fail closed.

    Does not launch commands, modify queues, enable projects, or grant scope.
    Callers must hold the returned lease throughout their entire job.
    """
    try:
        if not isinstance(project_id, str) or not project_id:
            raise AdmissionError('invalid project identity')
        projects = project_registry.load_projects(registry_root)
        project = projects.get(project_id)
        if project is None or not project_registry.project_enabled(project):
            raise AdmissionError('project is not enabled in authoritative registry')
        if lock_path.is_symlink() or lock_path.parent.is_symlink():
            raise AdmissionError('unsafe global lock path')
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if lock_path.parent.is_symlink():
            raise AdmissionError('unsafe global lock directory')
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise AdmissionError('unsafe global lock file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                return None
            return Lease(fd, project_id)
        except BaseException:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise
    except (OSError, ValueError, project_registry.ProjectPolicyError) as exc:
        raise AdmissionError('global admission cannot be verified') from exc
