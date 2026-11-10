#!/usr/bin/env python3
"""Read-only readiness check for a safe, isolated KÖL V4 PR E2E run.

Gate stays closed until the live owner-gateway, Stage01B and publisher all
target the same clean, registered checkout and the fixed main base branch.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path("/home/agent/projects/ai-prof-control-center")
REGISTRY = ROOT / "orchestrator/projects.json"
ORIGINAL = Path("/home/agent/Загрузки/kol-travel-platform")
ISOLATED = Path("/home/agent/projects/kol-ai-prof-v4-runner")
GATEWAY_MODULE = ROOT / "orchestrator/kol_publication_contract_v4.py"
PUBLISHER_MODULE = ROOT / "orchestrator/approved_task_publisher.py"
SERVER_UNIT = Path("/etc/systemd/system/ai-prof-control-center.service")
CHECKOUT_TARGET = "kol-v4-pr"
MAIN_SHA = "0a04ba6659da9b5b9b830aa301ab1c7dc684c27e"


def _git(path: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), *args], text=True,
            capture_output=True, timeout=12, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _release_audit_ok(checkout: Path) -> bool:
    """Use npm's exact high-severity gate; never run npm audit fix."""
    for extra in (("--omit=dev",), ()):
        try:
            result = subprocess.run(
                ["npm", "audit", "--audit-level=high", *extra, "--json"],
                cwd=str(checkout), text=True,
                capture_output=True, timeout=45, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
    return True


def _service_writable(checkout: Path) -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "show", "ai-prof-control-center.service",
             "--property=ReadWritePaths", "--value"],
            capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and str(checkout) in result.stdout.strip().split()


def checks(registry: Path = REGISTRY, *, original: Path = ORIGINAL,
           isolated: Path = ISOLATED,
           gateway_module: Path = GATEWAY_MODULE,
           publisher_module: Path = PUBLISHER_MODULE) -> dict:
    reasons: list[str] = []
    facts = {}
    try:
        projects = json.loads(registry.read_text(encoding="utf-8"))["projects"]
        project = next(p for p in projects if p.get("project_id") == CHECKOUT_TARGET)
    except (OSError, ValueError, KeyError, StopIteration, TypeError):
        return {"ready": False, "reasons": ["Реестр проектов не читается"], "facts": {}}
    facts["original_branch"] = _git(original, "branch", "--show-current")
    facts["original_status"] = _git(original, "status", "--porcelain=v1")
    facts["isolated_sha"] = _git(isolated, "rev-parse", "HEAD")
    facts["isolated_branch"] = _git(isolated, "branch", "--show-current")
    facts["isolated_status"] = _git(isolated, "status", "--porcelain=v1")
    if facts["isolated_branch"] != "main":
        reasons.append("PR-исполнитель должен находиться только на чистой ветке main")
    facts["isolated_git_metadata"] = (
        "directory" if (isolated / ".git").is_dir()
        else "worktree_pointer" if (isolated / ".git").is_file()
        else "missing"
    )
    if facts["isolated_sha"] != MAIN_SHA:
        reasons.append("Изолированная копия не находится на проверенном SHA main")
    if facts["isolated_status"] is None or facts["isolated_status"]:
        reasons.append("Изолированная копия недоступна или содержит изменения")
    if _git(original, "rev-parse", "main") != MAIN_SHA:
        reasons.append("Основание main исходного репозитория изменилось")
    if facts["original_status"] is None:
        reasons.append("Исходная рабочая копия KÖL недоступна")
    if project.get("enabled") is False:
        reasons.append("Проект KÖL отключён")
    if any(project.get(key) is not False for key in (
        "allow_commits", "allow_push", "allow_merge", "allow_deployment"
    )):
        reasons.append("Общие права записи KÖL стали слишком широкими")
    facts["registered_project_path"] = project.get("path")
    facts["registered_base_branch"] = project.get("base_branch")
    if project.get("path") != str(isolated):
        reasons.append("Реестр исполнителя направляет задачи в исходную папку, а не в изоляцию")
    if project.get("base_branch") != "main":
        reasons.append("Реестр исполнителя требует интеграционную ветку вместо main")
    for source, label, token in (
        (gateway_module, "GitHub Gateway V4", f'PROJECT_PATH = Path("{isolated}")'),
        (publisher_module, "PR Publisher", f'KOL_PROJECT = Path("{isolated}")'),
    ):
        try:
            body = source.read_text(encoding="utf-8")
            if token not in body:
                reasons.append(label + " ещё привязан к исходному пути")
        except OSError:
            reasons.append(label + " недоступен")
    if facts["isolated_git_metadata"] != "directory":
        reasons.append("Текущий PR Publisher требует отдельный каталог .git")
    facts["isolated_origin"] = _git(isolated, "remote", "get-url", "origin")
    if facts["isolated_origin"] != "git@github.com:stvelikiy-star/kol-travel-platform.git":
        reasons.append("Origin изолированного Git-клона должен быть доверенным KÖL SSH")
    facts["systemd_writable_checkout"] = _service_writable(isolated)
    if not facts["systemd_writable_checkout"]:
        reasons.append("Службе Control Center не разрешена запись в изолированный клон")
    facts["node_dependencies_present"] = all(
        (isolated / "node_modules/.bin" / name).exists()
        for name in ("tsc", "next", "eslint")
    )
    if not facts["node_dependencies_present"]:
        reasons.append("В изолированном клоне ещё не установлены зависимости Node.js")
    facts["npm_high_severity_audit_pass"] = (
        facts["node_dependencies_present"] and _release_audit_ok(isolated)
    )
    if not facts["npm_high_severity_audit_pass"]:
        reasons.append("Базовая версия KÖL не прошла обязательную проверку npm audit")
    return {"ready": not reasons, "reasons": reasons, "facts": facts}


def main() -> int:
    state = checks()
    print(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2))
    print("KOL_V4_PR_PREFLIGHT_" + ("PASS" if state["ready"] else "BLOCKED"))
    return 0 if state["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
