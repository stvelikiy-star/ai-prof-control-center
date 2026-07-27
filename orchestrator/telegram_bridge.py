#!/usr/bin/env python3
"""Secure Telegram polling bridge for AI PROF task intake."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path.home() / ".config/ai-prof-control-center/telegram.env"
STATE_DIR = Path.home() / ".local/state/ai-prof-control-center"
OFFSET_PATH = STATE_DIR / "telegram-update-offset"
SUBMIT_TASK = ROOT / "orchestrator/submit_task.py"
PROJECTS_PATH = ROOT / "orchestrator/projects.json"
ENV_KEYS = {
    "AI_PROF_TELEGRAM_BOT_TOKEN",
    "AI_PROF_TELEGRAM_REPORT_CHAT_ID",
    "AI_PROF_TELEGRAM_OWNER_USER_ID",
}
TOKEN_RE = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{20,}")
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(token|secret|password|credential|api[_-]?key)(\s*[=:]\s*)([^\s,;]+)"
)
HELP = (
    "AI PROF commands:\n"
    "/ai help\n"
    "/ai status\n"
    "/ai task <project> | <title> | <instructions>\n"
    "/ai task <text>  (project: ak-bermet)"
)


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    token: str
    chat_id: int
    owner_user_id: int


@dataclass(frozen=True)
class Command:
    name: str
    project: str = ""
    title: str = ""
    instructions: str = ""
    plain: bool = False


def redact(value: object) -> str:
    text = str(value)
    text = TOKEN_RE.sub("[REDACTED]", text)
    text = SECRET_VALUE_RE.sub(r"\1\2[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def load_config(path: Path = CONFIG_PATH) -> Config:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BridgeError(f"cannot read configuration: {path}") from exc
    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise BridgeError(f"invalid configuration line {number}")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in ENV_KEYS:
            raise BridgeError(f"unsupported configuration variable: {key}")
        if key in values:
            raise BridgeError(f"duplicate configuration variable: {key}")
        values[key] = value
    missing = sorted(ENV_KEYS - values.keys())
    if missing or any(not values[key] for key in ENV_KEYS):
        raise BridgeError("configuration is incomplete")
    try:
        chat_id = int(values["AI_PROF_TELEGRAM_REPORT_CHAT_ID"])
        owner_id = int(values["AI_PROF_TELEGRAM_OWNER_USER_ID"])
    except ValueError as exc:
        raise BridgeError("chat and owner IDs must be integers") from exc
    token = values["AI_PROF_TELEGRAM_BOT_TOKEN"]
    if not TOKEN_RE.fullmatch(token):
        raise BridgeError("bot token has an invalid format")
    return Config(token, chat_id, owner_id)


def authorized(message: object, config: Config) -> bool:
    if not isinstance(message, dict):
        return False
    chat = message.get("chat")
    sender = message.get("from")
    return (
        isinstance(chat, dict)
        and isinstance(sender, dict)
        and chat.get("id") == config.chat_id
        and sender.get("id") == config.owner_user_id
    )


def parse_command(text: object) -> Command | None:
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text.startswith("/ai"):
        return None
    prefix, separator, remainder = text.partition(" ")
    if prefix.split("@", 1)[0] != "/ai":
        return None
    remainder = remainder.strip() if separator else ""
    if remainder == "help":
        return Command("help")
    if remainder == "status":
        return Command("status")
    if not remainder.startswith("task"):
        return Command("invalid")
    payload = remainder[4:].strip()
    if not payload:
        return Command("invalid")
    parts = [part.strip() for part in payload.split("|")]
    if len(parts) == 1:
        return Command("task", "ak-bermet", parts[0], parts[0], True)
    if len(parts) == 3 and all(parts):
        return Command("task", parts[0], parts[1], parts[2])
    return Command("invalid")


def load_projects(path: Path = PROJECTS_PATH) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BridgeError("project registry is unavailable") from exc
    if data.get("version") != 1 or not isinstance(data.get("projects"), list):
        raise BridgeError("project registry is invalid")
    projects = {}
    for item in data["projects"]:
        if not isinstance(item, dict) or not isinstance(item.get("project_id"), str):
            raise BridgeError("project registry is invalid")
        projects[item["project_id"]] = item
    return projects


def resolve_project(requested: str, projects: dict[str, dict]) -> tuple[str, dict]:
    if requested not in projects:
        raise BridgeError(f"unknown project: {requested}")
    return requested, projects[requested]


def _scope_candidate(pattern: str) -> str:
    return pattern[:-3] if pattern.endswith("/**") else pattern


def select_scope(command: Command, project_id: str, project: dict) -> str:
    """Choose one concrete path, exclusively from the project's allowlist."""
    allowed = project.get("allowed_scope")
    if not isinstance(allowed, list) or not allowed or not all(
        isinstance(pattern, str) and pattern for pattern in allowed
    ):
        raise BridgeError("project registry entry has no valid allowed scopes")

    candidates = [(pattern, _scope_candidate(pattern)) for pattern in allowed]
    if any(not candidate for _pattern, candidate in candidates):
        raise BridgeError("project registry contains an unsafe scope")

    text = f"{command.title} {command.instructions}".lower()
    preferences: list[str] = []
    if re.search(r"\b(supabase|migration|migrations|database|schema|sql)\b", text):
        preferences.append("supabase/migrations")
    if re.search(r"\b(readme|read me)\b", text):
        preferences.append("README.md")
    if re.search(r"\b(test|tests|testing|spec|specs)\b", text):
        preferences.append("tests")
    if re.search(r"\b(doc|docs|documentation|guide|manual)\b", text):
        preferences.append("docs")
    if project_id == "ak-bermet":
        # General AK BERMET application work is constrained to application source.
        preferences.append("src")

    for preference in preferences:
        for _pattern, candidate in candidates:
            if candidate == preference:
                return candidate
    # Exact files are narrower than directory trees; registry order breaks ties.
    return min(candidates, key=lambda item: (item[0].endswith("/**"), len(item[1])))[1]


def make_work_branch(command: Command, project_id: str, project: dict) -> str:
    prefixes = project.get("work_prefixes")
    if not isinstance(prefixes, list):
        raise BridgeError("project registry entry has no valid branch prefixes")
    valid = [
        prefix for prefix in prefixes
        if isinstance(prefix, str) and re.fullmatch(r"(?:feature|fix)/", prefix)
    ]
    if not valid:
        raise BridgeError("project registry entry has no safe branch prefix")
    prefix = "feature/" if "feature/" in valid else valid[0]
    digest = hashlib.sha256(
        f"{project_id}\0{command.title}\0{command.instructions}".encode("utf-8")
    ).hexdigest()[:8]
    return f"{prefix}telegram-{digest}-{secrets.token_hex(6)}"


def submit_arguments(command: Command, projects: dict[str, dict]) -> tuple[list[str], str]:
    project_id, project = resolve_project(command.project, projects)
    scope = select_scope(command, project_id, project)
    work_branch = make_work_branch(command, project_id, project)
    args = [
        sys.executable, str(SUBMIT_TASK),
        "--root", str(ROOT),
        "--state-root", str(STATE_DIR),
        "--json", "create",
        "--project", project_id, "--title", command.title,
        "--instructions", command.instructions,
        "--work-branch", work_branch,
        "--scope", scope,
    ]
    return args, project_id


def rejection_reason(
    result: subprocess.CompletedProcess[str], command: Command | None = None,
) -> str:
    reason = ""
    try:
        payload = json.loads(result.stdout)
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            reason = payload["error"]
    except (TypeError, ValueError):
        pass
    if not reason:
        reason = "task intake rejected the request"
    reason = redact(reason).replace("\r", " ").replace("\n", " ").strip()
    if command is not None:
        for supplied_text in (command.instructions, command.title):
            if supplied_text:
                reason = reason.replace(supplied_text, "[request text]")
    return reason[:197] + "..." if len(reason) > 200 else reason


def submit(command: Command, projects: dict[str, dict]) -> tuple[str, str]:
    args, project_id = submit_arguments(command, projects)
    try:
        result = subprocess.run(
            args, cwd=ROOT, text=True, capture_output=True, timeout=30, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeError("task intake is unavailable") from exc
    if result.returncode != 0:
        raise BridgeError(rejection_reason(result, command))
    try:
        payload = json.loads(result.stdout)
        task_id = payload["task_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise BridgeError("task intake returned an invalid response") from exc
    if not isinstance(task_id, str):
        raise BridgeError("task intake returned an invalid task ID")
    return task_id, project_id


class TelegramClient:
    def __init__(self, config: Config, *, timeout: float = 35, retries: int = 3):
        self.config = config
        self.timeout = timeout
        self.retries = retries
        self.base_url = f"https://api.telegram.org/bot{config.token}/"

    def call(self, method: str, params: dict[str, object]) -> object:
        body = urllib.parse.urlencode(params).encode("utf-8")
        request = urllib.request.Request(self.base_url + method, data=body)
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not payload.get("ok"):
                    raise BridgeError("Telegram API rejected the request")
                return payload["result"]
            except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
        raise BridgeError("Telegram API request failed") from last_error

    def send(self, text: str) -> None:
        self.call("sendMessage", {"chat_id": self.config.chat_id, "text": text})


def read_offset(path: Path = OFFSET_PATH) -> int:
    try:
        return max(0, int(path.read_text(encoding="ascii").strip()))
    except FileNotFoundError:
        return 0
    except (OSError, ValueError) as exc:
        raise BridgeError("invalid update offset state") from exc


def write_offset(offset: int, path: Path = OFFSET_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".telegram-offset-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(f"{offset}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def handle_update(update: object, config: Config, client: TelegramClient) -> None:
    if not isinstance(update, dict):
        return
    message = update.get("message")
    if not authorized(message, config):
        return
    command = parse_command(message.get("text"))
    if command is None:
        return
    if command.name in {"help", "invalid"}:
        client.send(HELP)
    elif command.name == "status":
        projects = ", ".join(sorted(load_projects()))
        client.send(f"AI PROF bridge is running. Registered projects: {projects}")
    elif command.name == "task":
        try:
            task_id, project_id = submit(command, load_projects())
            client.send(f"Task created: {task_id}\nProject: {project_id}")
        except BridgeError as exc:
            client.send(f"Task not created: {redact(exc)}")


def run() -> None:
    config = load_config()
    client = TelegramClient(config)
    offset = read_offset()
    while True:
        try:
            updates = client.call("getUpdates", {"offset": offset, "timeout": 25})
            if not isinstance(updates, list):
                raise BridgeError("Telegram returned invalid updates")
            for update in updates:
                update_id = update.get("update_id") if isinstance(update, dict) else None
                if not isinstance(update_id, int):
                    continue
                # Checkpoint before side effects. If acknowledgement delivery fails
                # after intake succeeds, Telegram must not create a duplicate task.
                offset = max(offset, update_id + 1)
                write_offset(offset)
                handle_update(update, config, client)
        except BridgeError as exc:
            logging.error("bridge polling error: %s", exc)
            time.sleep(5)


def main() -> int:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(levelname)s %(message)s")
    try:
        run()
    except (BridgeError, KeyboardInterrupt) as exc:
        if isinstance(exc, BridgeError):
            logging.error("bridge stopped: %s", exc)
            return 1
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
