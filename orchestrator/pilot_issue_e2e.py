#!/usr/bin/env python3
"""Single-issue private AI PROF pilot: GitHub -> Codex -> tests -> draft PR.

Fixed project/repository/issue/scope. No merge, deploy, sudo or cross-project write.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = "stvelikiy-star/ai-prof-autonomy-pilot"
ISSUE = 1
DIR = Path("/home/agent/projects/ai-prof-pilot-sandbox")
STATE = Path("/home/agent/.local/state/ai-prof-control-center/reports/pilot-issue-1-e2e.json")
LOCK = Path("/home/agent/.local/state/ai-prof-control-center/run/pilot-issue-1-e2e.lock")
BRANCH = "feature/ai-prof-pilot-issue-1"
SCOPE = frozenset(("scripts/task_health.py", "tests/test_task_health.py"))
MARKER = "AI-PROF-PILOT-E2E-V1"
EXPECTED_TITLE = "[AI-PROF-PILOT] Add task health summary"
BASE = "main"
EXPECTED_BODY_SHA256 = None
# Optional fixed-authority outer gateway approval. In the autonomous lane,
# verify the main issue is still open and unchanged before any publication.
PRE_PUBLISH_CHECK = None
INDEPENDENT_CHECKS = (
    "from scripts.task_health import summarize_task\n"
    "assert summarize_task({'task_id':'a','checks':[{'name':'a','status':'PASS'}]})=='ГОТОВО'\n"
    "assert summarize_task({'task_id':'a','checks':[]})=='ОЖИДАНИЕ: нет проверок'\n"
    "assert summarize_task({'task_id':'a','checks':[{'name':'x','status':'FAIL'},{'name':'y','status':'PENDING'}]})=='ОШИБКИ: x; ОЖИДАНИЕ: y'\n"
)
CODEX = "/home/agent/.local/bin/codex"
PROMPT_SUFFIX = (
    "Act only in this private AI PROF pilot checkout. Modify ONLY "
    "scripts/task_health.py and tests/test_task_health.py. "
    "Do not modify Git metadata, commit, push, use network or other projects, "
    "do not change services, secrets, or databases. "
    "Run python3 -B -m unittest discover -s tests -p 'test_*.py' -q."
)


class PilotError(RuntimeError):
    pass


def run(args: list[str], *, cwd: Path | None = None, timeout: int = 30,
        input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args, cwd=str(cwd) if cwd else None, text=True,
        input=input_text, stdin=subprocess.DEVNULL if input_text is None else None,
        capture_output=True, timeout=timeout, check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0",
             "GH_PROMPT_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode:
        raise PilotError(f"{args[0]} exited {result.returncode}")
    return result


def git(*args: str, timeout: int = 30) -> str:
    return run(["git", *args], cwd=DIR, timeout=timeout).stdout.rstrip("\n")


def github(resource: str) -> dict:
    raw = run(["gh", "api", "-X", "GET", resource], timeout=35).stdout
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise PilotError("GitHub returned invalid response")
    return value


def record(phase: str, **extras: object) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"phase": phase, "issue": ISSUE, "repository": REPO,
            "timestamp": int(time.time()), **extras}
    temp = STATE.with_suffix(".pending")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(STATE)


def validate_issue(issue: dict) -> str:
    if issue.get("number") != ISSUE or issue.get("state") != "open":
        raise PilotError("Issue number/state mismatch")
    if issue.get("user", {}).get("login") != "stvelikiy-star":
        raise PilotError("Not authored by fixed owner")
    if issue.get("title") != EXPECTED_TITLE:
        raise PilotError("Unexpected issue title")
    body = issue.get("body")
    if not isinstance(body, str) or not body.startswith(MARKER + "\n"):
        raise PilotError("Invalid issue contract marker")
    lines = body.splitlines()
    if "Scope-Files: " + ", ".join(sorted(SCOPE)) not in lines:
        raise PilotError("Unexpected scope")
    if "Base-Branch: " + BASE not in lines:
        raise PilotError("Unexpected branch")
    if EXPECTED_BODY_SHA256 is not None:
        import hashlib
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != EXPECTED_BODY_SHA256:
            raise PilotError("Issue body contract digest mismatch")
    return body


def validate_checkout() -> None:
    if DIR.resolve(strict=True) != DIR or not (DIR / ".git").is_dir():
        raise PilotError("Pilot checkout unavailable or symlinked")
    if git("remote", "get-url", "origin") != "git@github.com:" + REPO + ".git":
        raise PilotError("Pilot origin mismatch")
    if git("branch", "--show-current") != "develop":
        raise PilotError("Expected clean untouched develop branch")
    if git("status", "--porcelain=v1"):
        raise PilotError("Pilot checkout is dirty")
    remote_main = git("ls-remote", "origin", "refs/heads/" + BASE).split()[0]
    if remote_main != git("rev-parse", BASE) or git("rev-parse", "HEAD") != remote_main:
        raise PilotError("Main SHA mismatch")
    probe = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/heads/" + BRANCH],
        cwd=str(DIR), capture_output=True, timeout=10, check=False,
    )
    if probe.returncode != 1:
        raise PilotError("Work branch already exists or Git is unhealthy")


def changed_files() -> set[str]:
    # All status entries must be in exactly the 2-file allowlist.
    result = git("status", "--porcelain=v1", "--untracked-files=all")
    paths = set()
    for line in result.splitlines():
        if len(line) < 4:
            raise PilotError("Malformed git status")
        name = line[3:].strip()
        if " -> " in name or name.startswith('"'):
            raise PilotError("Renames/quoted paths not allowed")
        paths.add(name)
    if not paths.issubset(SCOPE):
        raise PilotError("Codex touched files outside fixed scope: " + ", ".join(sorted(paths - SCOPE)))
    return paths


def execute() -> dict:
    issue = github(f"repos/{REPO}/issues/{ISSUE}")
    body = validate_issue(issue)
    validate_checkout()
    record("accepted", base_sha=git("rev-parse", "HEAD"))
    git("switch", "-c", BRANCH, BASE)
    record("codex_running")
    prompt = ("Implement this exact private issue.\n" + body + "\n\n" + PROMPT_SUFFIX)
    result = run([CODEX, "exec", "--ephemeral", "-s", "workspace-write",
                  "-C", str(DIR), prompt], cwd=DIR, timeout=240)
    if "Ran " not in result.stdout + result.stderr:
        # Codex may omit test output, independent test always runs below.
        pass
    return finalize()


def finalize() -> dict:
    paths = changed_files()
    if not paths or paths != set(SCOPE):
        raise PilotError("Codex did not edit required scoped source and tests")
    record("testing", files=sorted(paths))
    test = run(["python3", "-B", "-m", "unittest", "discover",
                "-s", "tests", "-p", "test_*.py", "-q"], cwd=DIR, timeout=50)
    if "Ran " not in test.stderr + test.stdout or "OK" not in test.stderr + test.stdout:
        raise PilotError("Independent test output invalid")
    # Independent semantic assertions beyond agent-authored tests.
    run(["python3", "-B", "-c", INDEPENDENT_CHECKS], cwd=DIR, timeout=20)
    if changed_files() != paths:
        raise PilotError("Unstable file set")
    validate_issue(github(f"repos/{REPO}/issues/{ISSUE}"))
    if PRE_PUBLISH_CHECK is not None:
        PRE_PUBLISH_CHECK()
    git("add", "--", *sorted(paths))
    staged = set(git("diff", "--cached", "--name-only").splitlines())
    if staged != paths:
        raise PilotError("Staged scope mismatch")
    git("-c", "commit.gpgSign=false", "commit", "--no-gpg-sign", "-m",
        f"feat(pilot): implement GitHub issue {ISSUE} via Codex")
    commit = git("rev-parse", "HEAD")
    record("pushing", commit=commit)
    validate_issue(github(f"repos/{REPO}/issues/{ISSUE}"))
    if PRE_PUBLISH_CHECK is not None:
        PRE_PUBLISH_CHECK()
    git("push", "-u", "origin", f"refs/heads/{BRANCH}:refs/heads/{BRANCH}", timeout=90)
    remote = git("ls-remote", "origin", "refs/heads/" + BRANCH).split()[0]
    if remote != commit:
        raise PilotError("Remote commit SHA mismatch")
    if PRE_PUBLISH_CHECK is not None:
        PRE_PUBLISH_CHECK()
    pr_payload = run(["gh", "api", "-X", "POST", f"repos/{REPO}/pulls",
        "-f", f"title=AI PROF autonomous pilot: {EXPECTED_TITLE}",
        "-f", f"head={BRANCH}", "-f", f"base={BASE}", "-F", "draft=true",
        "-f", f"body=Automated bounded AI PROF pilot from owner issue #{ISSUE}. "
              "Codex implementation and independent Python checks PASS. "
              "Only two approved files changed. No merge or deployment."],
        timeout=40).stdout
    pr = json.loads(pr_payload)
    if pr.get("draft") is not True or pr.get("head", {}).get("sha") != commit:
        raise PilotError("Draft PR evidence does not match commit")
    record("passed", commit=commit, pull_request=pr.get("html_url"),
           pull_request_number=pr.get("number"), independent_tests="PASS")
    return {"commit": commit, "url": pr.get("html_url"), "pr": pr.get("number")}


def resume_known_failed_scope_parser() -> dict:
    previous = json.loads(STATE.read_text(encoding="utf-8"))
    if (previous.get("phase") != "failed"
            or previous.get("error") !=
            "Codex touched files outside fixed scope: cripts/task_health.py"):
        raise PilotError("Resume requires exact known parser failure")
    validate_issue(github(f"repos/{REPO}/issues/{ISSUE}"))
    if DIR.resolve(strict=True) != DIR or not (DIR / ".git").is_dir():
        raise PilotError("Pilot directory unsafe")
    if git("remote", "get-url", "origin") != "git@github.com:" + REPO + ".git":
        raise PilotError("Pilot remote changed")
    if git("branch", "--show-current") != BRANCH:
        raise PilotError("Pilot work branch changed")
    if git("rev-parse", "HEAD") != git("rev-parse", "main"):
        raise PilotError("Unexpected earlier commit in branch")
    changed = changed_files()
    if changed != SCOPE:
        raise PilotError("Unexpected files before resuming")
    record("resuming_after_scoped_parser_fix", files=sorted(changed))
    return finalize()


def notify_terminal_pass() -> int:
    data = json.loads(STATE.read_text(encoding="utf-8"))
    if data.get("phase") != "passed":
        raise PilotError("Cannot notify a non-PASS pilot")
    if data.get("notified") is True:
        print("PILOT_TELEGRAM_ALREADY_NOTIFIED")
        return 0
    url = data.get("pull_request")
    expected_prefix = f"https://github.com/{REPO}/pull/"
    if not isinstance(url, str) or url != expected_prefix + str(data.get("pull_request_number")):
        raise PilotError("Unexpected PR URL in saved acceptance")
    import telegram_bridge as bridge
    cfg = bridge.load_config()
    response = bridge.TelegramClient(cfg).call("sendMessage", {
        "chat_id": cfg.chat_id,
        "text": (
            "🤖 AI PROF — полный тестовый цикл Github → Codex → проверка → PR завершён.\n"
            f"✅ GitHub issue №{ISSUE} обработан.\n"
            "✅ Codex создал код; независимые проверки PASS.\n"
            f"✅ Draft PR №{data.get('pull_request_number')} содержит ровно два разрешённых файла.\n"
            f"PR: {url}\n"
            "🔒 Merge, production и KÖL не затронуты."
        ),
    })
    data["notified"] = True
    data["telegram_message_id"] = response.get("message_id")
    temp = STATE.with_suffix(".notify")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(STATE)
    print("PILOT_TELEGRAM_PASS message_id=" + str(data["telegram_message_id"]))
    return 0


def main() -> int:
    notify_only = "--notify-existing" in sys.argv[1:]
    resume = "--resume-known-parser-failure" in sys.argv[1:]
    if any(x not in ("--resume-known-parser-failure", "--notify-existing") for x in sys.argv[1:]) or (resume and notify_only):
        raise SystemExit("unsupported argument")
    LOCK.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("PILOT_E2E_ALREADY_RUNNING")
            return 3
        if notify_only:
            try:
                return notify_terminal_pass()
            except (OSError, ValueError, PilotError, RuntimeError) as exc:
                print("PILOT_TELEGRAM_RETRY", type(exc).__name__)
                return 1
        if STATE.exists() and not resume:
            print("PILOT_E2E_ALREADY_STARTED_NO_DUPLICATE")
            return 4
        if not STATE.exists() and resume:
            print("PILOT_E2E_NO_SAVED_STATE")
            return 4
        try:
            result = resume_known_failed_scope_parser() if resume else execute()
        except (OSError, ValueError, PilotError, subprocess.TimeoutExpired, IndexError) as exc:
            record("failed", error_type=type(exc).__name__, error=str(exc)[:240])
            print("PILOT_E2E_FAIL", type(exc).__name__, str(exc)[:240])
            return 1
        print("PILOT_E2E_PASS", result)
        try:
            return notify_terminal_pass()
        except (OSError, ValueError, PilotError, RuntimeError) as exc:
            print("PILOT_TELEGRAM_RETRY", type(exc).__name__)
            return 1


if __name__ == "__main__":
    sys.exit(main())
