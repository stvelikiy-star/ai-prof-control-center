#!/usr/bin/env python3
"""Fixed KÖL source audit using a nonsecret, immutable Git HEAD snapshot.

The agent only reads a filtered snapshot. Audit output is written to AI PROF
state, never to the client repository. No shell, Git writes, PR, or deployment.
"""
from __future__ import annotations

import io
import os
import re
import stat
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("/home/agent/Загрузки/kol-travel-platform")
CODEX = Path("/home/agent/.local/bin/codex")
STATE = Path("/home/agent/.local/state/ai-prof-control-center")
MAX_ARCHIVE_BYTES = 42_000_000
MAX_FILE_BYTES = 380_000
MAX_CONTENT_BYTES = 21_000_000
MAX_FILES = 1600
CODEX_TIMEOUT = 1800
INCLUDED_DIRS = (
    "src/", "docs/", "supabase/", "scripts/", "ops/",
    ".github/workflows/", "app/", "components/", "lib/",
)
INCLUDED_FILES = frozenset({
    "README.md", "CHECK_LOCAL_RUN.md", "package.json", "tsconfig.json",
    "Dockerfile", "eslint.config.mjs", "postcss.config.js",
    "tailwind.config.ts", ".env.example", "next.config.js",
    "next.config.mjs", "next.config.ts",
})
SENSITIVE_PARTS = frozenset({
    ".git", ".env", ".env.local", ".env.production", ".env.development",
    "node_modules", ".next", ".temp", "secrets", "credentials",
    "__pycache__", ".supabase",
})
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".sqlite", ".db", ".dump")
HEAD_RE = re.compile(r"^[a-f0-9]{40}$")
TASK_RE = re.compile(r"^KOL_TRAVEL_PLATFORM_[0-9]{8}T[0-9]{6}Z_[A-Z0-9]{6}$")


class AuditError(RuntimeError):
    pass


def _git(repo: Path, *args: str, timeout: int = 45) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/git", *args], cwd=repo, text=True,
            capture_output=True, check=False, shell=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError("read-only Git inspection unavailable") from exc
    if result.returncode:
        raise AuditError("read-only Git inspection failed")
    return result.stdout.strip()


def metadata(repo: Path = PROJECT) -> tuple[str, str, str]:
    if repo.is_symlink() or not repo.is_dir() or not (repo / ".git").is_dir():
        raise AuditError("configured KÖL repository missing or unsafe")
    branch = _git(repo, "branch", "--show-current")
    head = _git(repo, "rev-parse", "HEAD")
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if not HEAD_RE.fullmatch(head) or not branch or len(branch) > 128:
        raise AuditError("invalid KÖL Git HEAD or branch")
    return branch, head, status


def _allowed(name: str) -> bool:
    if name.startswith("/") or "\\" in name or "\x00" in name:
        return False
    parts = name.split("/")
    if any(part in ("", ".", "..") or part.casefold() in SENSITIVE_PARTS
           for part in parts):
        return False
    if name.casefold().endswith(SENSITIVE_SUFFIXES):
        return False
    if name.endswith(".env") and name != ".env.example":
        return False
    return name in INCLUDED_FILES or any(name.startswith(p) for p in INCLUDED_DIRS)


def build_snapshot(repo: Path, head: str, dest: Path) -> tuple[int, int]:
    """No tar extraction; explicitly copy only bounded, regular Git blobs."""
    try:
        archive = subprocess.run(
            ["/usr/bin/git", "archive", "--format=tar", head],
            cwd=repo, capture_output=True, check=False, shell=False, timeout=80,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuditError("Git archive unavailable") from exc
    if archive.returncode or len(archive.stdout) > MAX_ARCHIVE_BYTES:
        raise AuditError("Git archive failed or exceeded safety limit")
    count = size = 0
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as entries:
        for member in entries:
            if not member.isfile() or not _allowed(member.name):
                continue
            if not 0 < member.size <= MAX_FILE_BYTES:
                continue
            count += 1
            size += member.size
            if count > MAX_FILES or size > MAX_CONTENT_BYTES:
                raise AuditError("snapshot exceeds bounded size")
            target = dest / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise AuditError("duplicate or unsafe snapshot path")
            source = entries.extractfile(member)
            if source is None:
                raise AuditError("unreadable archived source")
            with source, target.open("xb") as output:
                output.write(source.read(member.size))
            target.chmod(0o444)
    if count < 20:
        raise AuditError("insufficient tracked source files for full KÖL audit")
    for folder in sorted(dest.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if folder.is_dir():
            folder.chmod(0o555)
    dest.chmod(0o555)
    return count, size


def _prompt(branch: str, head: str, dirty: bool, files: int) -> str:
    return f"""Ты — независимый технический аудитор проекта KÖL. Ответ строго на русском,
в виде развёрнутого доказательного Markdown-отчёта. Доступна безопасная КОПИЯ
отслеживаемых файлов Git, а не исходный работающий проект. Не выдумывай проверки.
Git branch: {branch}; HEAD: {head}; файлов в снимке: {files}.
Незакоммиченные файлы в оригинале: {"есть (в снимок НЕ входят)" if dirty else "нет"}.
Твоя задача — ИССЛЕДОВАТЬ исходники, а не менять их: работай только на чтение.
Не ищи секреты, токены, .env, приватные ключи или клиентские персональные данные.
Не запускай деплой, миграции, SQL, установку зависимостей и веб-запросы.
Разрешены чтение содержимого, grep/rg/find, package.json, существующие тесты и документация.

Проанализируй:
1. Архитектуру проекта, стек, структуру, состояние репозитория и ветки (только известное).
2. Текущие функции: каталог/жильё/туры, заявки, Owner/Admin/Partner/Courier/Client,
   маршруты, CRM/оператор, цены, доступные даты, RU/KG/EN и мобильный UX.
3. API, Supabase PostgreSQL/RLS, миграции, права, авторизацию, данные и разрывы контрактов.
4. Интеграции и обработку ошибок: реальные сценарии, уведомления, маршрутизация.
5. Безопасность, API keys только на уровне архитектуры (без раскрытия значений),
   нарушение изоляции пользователей и отсутствие тестов.
6. CI/CD, тесты, сборку, бэкапы/restore, окружение, деплой (если не проверено — NOT VERIFIED).
7. Реальные дефекты с точными путями/функциями и воспроизводимыми доказательствами.
8. Матрицу PASS/FAIL/NOT VERIFIED для критичных функций, НЕ выдавая наличие кода за E2E.
9. P0/P1/P2, зависимости, риски, ожидаемую ценность.
10. Генеральный план по этапам: очередность конкретных ИСПОЛНЯЕМЫХ AI PROF задач
    с Scope-Files, проверками, критерием завершения, обратимым действием;
    отдельно что можно делегировать AI PROF автономно в текущих разрешениях,
    а что потребует подтверждения владельца/реальной среды.
11. Перечень конкретно недостающих данных для запуска.

Формат: заголовок; методология и ограничения; краткий executive summary;
таблица готовности; 8-15 проверенных находок со ссылками на конкретные
пути/строки/экспортируемые функции; поэтапная дорожная карта; критерии GO/NO-GO.
Кратко цитируй строки исходника лишь при крайней необходимости.
Везде явно отделяй источник в коде от непроверенной внешней среды.
Если ничего не удалось проверить по пункту, пометь NOT VERIFIED.
Нельзя выводить в ответ токены, пароли, содержимое приватных файлов.
"""


def _redact(text: str) -> str:
    # Disclosure-safe final artifact; exhaustive scanning is not assumed.
    patterns = (
        re.compile(r"(?i)\b(token|secret|password|api[_-]?key|authorization)\s*[:=]\s*\S+"),
        re.compile(r"\b(?:sk|sbp)_[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"(?i)\b(?:postgres(?:ql)?|mongodb|redis)://\S+"),
    )
    for pattern in patterns:
        text = pattern.sub("[REDACTED]", text)
    return text


def run(task_id: str, *, repo: Path = PROJECT,
        state: Path = STATE, codex: Path = CODEX) -> Path:
    if not TASK_RE.fullmatch(task_id):
        raise AuditError("invalid KÖL audit task identifier")
    if not codex.is_file() or not os.access(codex, os.X_OK):
        raise AuditError("Codex CLI unavailable")
    branch, head, dirty = metadata(repo)
    with tempfile.TemporaryDirectory(prefix="ai-prof-kol-audit-snapshot-") as directory:
        snapshot = Path(directory) / "source"
        snapshot.mkdir(mode=0o700)
        count, _bytes = build_snapshot(repo, head, snapshot)
        output = Path(directory) / "last-message.md"
        env = os.environ.copy()
        for key in list(env):
            lowered = key.casefold()
            if any(word in lowered for word in (
                "token", "password", "secret", "apikey", "api_key",
                "database_url", "supabase_", "service_role", "github_", "green_",
            )):
                env.pop(key, None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        argv = [
            str(codex), "-a", "never", "--disable", "plugins",
            "-m", "gpt-5.6-sol", "-c", 'model_reasoning_effort="high"',
            "exec", "-s", "read-only", "--skip-git-repo-check", "--ephemeral",
            "-C", str(snapshot), "-o", str(output), "-",
        ]
        try:
            result = subprocess.run(
                argv, input=_prompt(branch, head, bool(dirty), count),
                text=True, capture_output=True, env=env, shell=False,
                check=False, timeout=CODEX_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AuditError("read-only Codex audit unavailable or timed out") from exc
        if result.returncode or not output.is_file() or output.is_symlink():
            raise AuditError("read-only Codex audit failed; see stage logs")
        if output.stat().st_size > 120_000:
            raise AuditError("agent report exceeds maximum length")
        report = _redact(output.read_text(encoding="utf-8"))
        if len(report) < 2500 or not all(word in report.casefold() for word in (
            "supabase", "p0", "p1", "not verified",
        )):
            raise AuditError("audit report too short or missing key evidence")
        if any(path.is_file() and (path.stat().st_mode & 0o222)
               for path in snapshot.rglob("*")):
            raise AuditError("read-only snapshot was modified")
    if metadata(repo) != (branch, head, dirty):
        raise AuditError("original KÖL repository changed during audit")

    target_root = state / "reports"
    target_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = target_root / f"{task_id}.md"
    if target.exists() or target.is_symlink():
        raise AuditError("audit report already exists; refusing overwrite")
    stamp = datetime.now(timezone.utc).isoformat()
    body = (
        f"# AI PROF — KÖL read-only audit ({task_id})\n\n"
        f"_Created UTC: {stamp}; Git HEAD: {head}; branch: {branch}; "
        f"tracked files snapshot: {count}; uncommitted files: "
        f"{'excluded from audit' if dirty else 'none'}._\n\n"
        + report.strip() + "\n"
    )
    fd, tmp = tempfile.mkstemp(prefix=".kol-audit-", dir=target_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target
