"""Read-only summary of the autonomous queue filesystem."""

from __future__ import annotations

import os
from pathlib import Path
import stat


_QUEUE_DIRECTORIES = (
    ("pending", "autonomous_pending"),
    ("active", "autonomous_active"),
    ("completed", "autonomous_completed"),
    ("blocked", "autonomous_blocked"),
)


def summarize_queue(root: Path) -> dict:
    """Return regular-file counts for the four autonomous queue directories.

    Missing queue directories count as empty.  Symlinks and filesystem objects
    other than regular files/directories are rejected instead of followed or
    silently ignored.
    """

    if not isinstance(root, Path):
        raise TypeError("root must be a pathlib.Path")

    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return {name: 0 for name, _ in _QUEUE_DIRECTORIES}

    if stat.S_ISLNK(root_stat.st_mode):
        raise ValueError("queue root must not be a symlink")
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("queue root must be a directory")

    summary = {}
    for name, directory_name in _QUEUE_DIRECTORIES:
        directory = root / directory_name
        try:
            directory_stat = directory.lstat()
        except FileNotFoundError:
            summary[name] = 0
            continue

        if stat.S_ISLNK(directory_stat.st_mode):
            raise ValueError(f"queue directory must not be a symlink: {directory_name}")
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise ValueError(f"queue path must be a directory: {directory_name}")

        count = 0
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ValueError(
                        f"queue entry must be a regular file: {directory_name}/{entry.name}"
                    )
                count += 1
        summary[name] = count

    return summary
