#!/usr/bin/env python3
"""Fail-closed encrypted AI PROF off-site SFTP backup.

Config is a rootless user-only JSON under ~/.config/ai-prof-control-center.
Never uploads recovery passphrases or unencrypted task queues.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ARCHIVES = Path("/home/agent/ai-prof-backups/control-plane-encrypted")
N8N_ARCHIVES = Path("/home/agent/ai-prof-backups/n8n-offsite-encrypted")
CONFIG = Path("/home/agent/.config/ai-prof-control-center/offsite-sftp.json")
STATE = Path("/home/agent/.local/state/ai-prof-control-center")
NAME = re.compile(r"control-plane-\d{8}T\d{6}Z\.tar\.gz\.gpg")
N8N_NAME = re.compile(r"n8n-ai-prof-20\d{2}-\d{2}-\d{2}_\d{6}\.tar\.gz\.gpg")
HOST = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")
USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
PATH = re.compile(r"^/[A-Za-z0-9_./-]+$")
MAX_BYTES = 20_000_000


class OffsiteError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            d.update(chunk)
    return d.hexdigest()


def config_from_file(path: Path = CONFIG) -> dict:
    if path.is_symlink() or not path.is_file():
        raise OffsiteError("настройки SFTP отсутствуют")
    if path.stat().st_mode & 0o077:
        raise OffsiteError("небезопасные права файла настроек SFTP")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OffsiteError("невозможно прочитать настройки SFTP") from exc
    if not isinstance(config, dict):
        raise OffsiteError("неверный формат настроек")
    host, user, remote = (config.get(key) for key in ("host", "user", "remote_dir"))
    port = config.get("port", 22)
    key_file, known_hosts = (config.get(key) for key in ("identity_file", "known_hosts"))
    if not isinstance(host, str) or not HOST.fullmatch(host):
        raise OffsiteError("неверный адрес SFTP")
    if not isinstance(user, str) or not USER.fullmatch(user):
        raise OffsiteError("неверный пользователь SFTP")
    if type(port) is not int or not 1 <= port <= 65535:
        raise OffsiteError("неверный порт SFTP")
    if (not isinstance(remote, str) or not PATH.fullmatch(remote)
            or "/../" in remote or remote.endswith("/..") or "//" in remote
            or remote.rstrip("/") == "/"):
        raise OffsiteError("небезопасный каталог SFTP")
    for key, value in (("identity_file", key_file), ("known_hosts", known_hosts)):
        if not isinstance(value, str) or not value.startswith("/"):
            raise OffsiteError("неверный путь " + key)
        target = Path(value)
        if target.is_symlink() or not target.is_file() or target.stat().st_mode & 0o077:
            raise OffsiteError("небезопасный файл " + key)
    return config


def latest_verified(directory: Path = ARCHIVES) -> tuple[Path, Path, str]:
    pattern = N8N_NAME if directory == N8N_ARCHIVES else NAME
    files = [p for p in directory.glob("*.tar.gz.gpg")
             if p.is_file() and not p.is_symlink() and pattern.fullmatch(p.name)]
    if not files:
        raise OffsiteError("нет зашифрованного архива AI PROF")
    archive = max(files, key=lambda p: p.stat().st_mtime_ns)
    if archive.stat().st_size < 100 or archive.stat().st_size > MAX_BYTES:
        raise OffsiteError("размер архива недопустим")
    digest = _sha256(archive)
    checksum = Path(str(archive) + ".sha256")
    if checksum.is_symlink() or not checksum.is_file():
        raise OffsiteError("нет контрольной суммы")
    values = checksum.read_text(encoding="ascii").strip().split(maxsplit=1)
    if len(values) != 2 or values[0] != digest or values[1].lstrip("*") not in (archive.name, str(archive)):
        raise OffsiteError("контрольная сумма архива неверна")
    return archive, checksum, digest


def sftp(config: dict, commands: list[str]) -> None:
    # Use a batch file, strict host key verification and no password prompts.
    # File names are fixed-format; remote_dir is validated above.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="ai-prof-sftp-", delete=True) as f:
        f.write("\n".join(commands) + "\n")
        f.flush()
        args = [
            "/usr/bin/sftp", "-q", "-b", f.name,
            "-P", str(config["port"]),
            "-oBatchMode=yes", "-oStrictHostKeyChecking=yes",
            "-oUserKnownHostsFile=" + config["known_hosts"],
            "-oIdentityFile=" + config["identity_file"],
            "-oConnectTimeout=15", "-oServerAliveInterval=10",
            config["user"] + "@" + config["host"],
        ]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OffsiteError("SFTP недоступен") from exc
        if result.returncode:
            raise OffsiteError("SFTP завершился с ошибкой; проверьте каталог и доступ")


def transfer(config: dict, archive: Path, checksum: Path, expected: str) -> None:
    remote = config["remote_dir"].rstrip("/")
    folder = remote + "/"
    # Do not silently create arbitrary directories on the remote server.
    with tempfile.TemporaryDirectory(prefix="ai-prof-offsite-readback-") as dirname:
        downloaded = Path(dirname) / archive.name
        # Upload to a temporary name; rename the full object on success.
        commands = [
            'put "' + str(archive) + '" "' + folder + archive.name + '.part"',
            'rename "' + folder + archive.name + '.part" "' + folder + archive.name + '"',
            'put "' + str(checksum) + '" "' + folder + checksum.name + '"',
            'get "' + folder + archive.name + '" "' + str(downloaded) + '"',
        ]
        sftp(config, commands)
        if not downloaded.is_file():
            raise OffsiteError("сервер не вернул архив при проверке")
        if _sha256(downloaded) != expected:
            raise OffsiteError("обратное скачивание: SHA256 не совпал")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()
    import fcntl
    try:
        prepared = [
            latest_verified(),
            latest_verified(N8N_ARCHIVES),
        ]
        config = config_from_file(args.config)
        if args.check_only:
            print("SFTP_CONFIG_PASS; TWO_ENCRYPTED_BACKUPS_PASS; TRANSFER_NOT_TESTED")
            return 0
        lock = STATE / "run/offsite-sftp.lock"
        lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock.open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise OffsiteError("другая передача уже выполняется") from exc
            for archive, checksum, digest in prepared:
                transfer(config, archive, checksum, digest)
        print("SFTP_OFFSITE_READBACK_PASS archives=" +
              ",".join(item[0].name for item in prepared))
        return 0
    except (OSError, OffsiteError) as exc:
        print("SFTP_OFFSITE_FAIL: " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
