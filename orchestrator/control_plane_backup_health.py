#!/usr/bin/env python3
"""Read-only local AI PROF state backup checksum and freshness gate."""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import re
import time

BACKUPS = Path("/home/agent/ai-prof-backups/control-plane-encrypted")
KEY = Path("/home/agent/.config/ai-prof-control-center/recovery-key.gpg-passphrase")
NAME = re.compile(r"control-plane-\d{8}T\d{6}Z\.tar\.gz\.gpg")


def evaluate(directory: Path = BACKUPS, key: Path = KEY, now: float | None = None) -> tuple[str, str]:
    current = time.time() if now is None else now
    try:
        if (not key.is_file() or key.is_symlink()
                or (key.stat().st_mode & 0o777) != 0o600
                or key.stat().st_size < 32):
            return "CRITICAL", "recovery key missing, empty or permissions unsafe"
    except OSError:
        return "CRITICAL", "recovery key inaccessible"
    try:
        files = [p for p in directory.iterdir() if NAME.fullmatch(p.name) and
                 p.is_file() and not p.is_symlink()]
    except OSError:
        return "CRITICAL", "control-plane backups unavailable"
    if not files:
        return "CRITICAL", "no encrypted control-plane snapshots"
    try:
        latest = max(files, key=lambda p: p.stat().st_mtime)
        if latest.stat().st_size == 0:
            return "CRITICAL", "encrypted snapshot is empty"
        age_hours = max(0, (current - latest.stat().st_mtime) / 3600)
        checksum = latest.with_name(latest.name + ".sha256")
        if checksum.is_symlink() or not checksum.is_file():
            return "CRITICAL", "snapshot checksum unavailable"
    except OSError:
        return "CRITICAL", "encrypted snapshot metadata unreadable"
    try:
        text = checksum.read_text(encoding="ascii").strip()
        digest, filename = text.split(maxsplit=1)
        # Accept portable basename manifests and legacy absolute-path manifests.
        if filename.lstrip("*") not in (latest.name, str(latest)) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return "CRITICAL", "snapshot checksum metadata mismatch"
        hash_obj = hashlib.sha256()
        with latest.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                hash_obj.update(block)
        if hash_obj.hexdigest() != digest:
            return "CRITICAL", "snapshot SHA256 mismatch"
    except (OSError, ValueError, UnicodeError):
        return "CRITICAL", "snapshot checksum unreadable"
    if age_hours >= 54:
        return "CRITICAL", f"encrypted snapshot stale {age_hours:.0f}h"
    if age_hours >= 30:
        return "WARNING", f"encrypted snapshot age {age_hours:.0f}h"
    return "PASS", f"local encrypted snapshot verified, age {age_hours:.0f}h"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backups", type=Path, default=BACKUPS)
    parser.add_argument("--key", type=Path, default=KEY)
    args = parser.parse_args()
    status, message = evaluate(args.backups, args.key)
    print(f"{status}|{message}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
