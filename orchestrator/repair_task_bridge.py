#!/usr/bin/env python3
"""Bridge validated incident diagnoses into the existing AI PROF task queue.

This bridge creates *code repair tasks only*. It never edits a target project,
never runs Codex itself, and never performs restart/deploy/merge/migration.
Every generated task uses the existing Task Schema V2 / submit_task safety
contract, project allowlists, branch rules, trusted test contract, required
checks, and downstream Stage01A -> repair -> independent Codex audit pipeline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import incident_diagnosis_runner as diagnosis_runner
import submit_task
from incident_engine import summary as incident_summary
from project_test_contracts import contract_for_project
from repair_policy import classify
from runtime_paths import initialize

DEFAULT_ROOT = Path("/home/agent/projects/ai-prof-control-center")
DEFAULT_STATE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center")
BRIDGE_VERSION = 1
RESULT_LIMIT = 128 * 1024
INCIDENT_ID_RE = diagnosis_runner.INCIDENT_ID_RE
REPAIR_ACTIONS = {"PREPARE_REPAIR_FOR_OWNER_REVIEW", "GREEN_RUNBOOK_CANDIDATE"}
TASK_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{5,79}$")

# Incident-origin automatic repair must never be able to rewrite the evidence
# that later proves the repair, or cross into authority/deployment/database
# surfaces. These files may still be changed through an explicitly scoped owner
# task, but they cannot be inferred from model-produced incident evidence.
AUTOMATIC_REPAIR_DENY_PREFIXES = (
    "tests/",
    ".github/workflows/",
    "systemd/",
    "scripts/",
    "supabase/migrations/",
    "automation/n8n/",
)
AUTOMATIC_REPAIR_DENY_EXACT = {
    "test_control_center.py",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "Dockerfile",
    "compose.yaml",
    "compose.production.yaml",
    "orchestrator/config.json",
    "orchestrator/projects.json",
    "orchestrator/project_test_contracts.json",
    "orchestrator/project_test_contracts.py",
    "orchestrator/project_recovery_contracts.json",
    "orchestrator/project_recovery_gate.py",
    "orchestrator/repair_operation_bindings.json",
    "orchestrator/repair_operation_bindings.py",
    "orchestrator/repair_policies.json",
    "orchestrator/repair_policy.py",
    "orchestrator/repair_runbooks.json",
    "orchestrator/runbook_registry.py",
    "orchestrator/operation_profiles.py",
    "orchestrator/operations_runner.py",
    "orchestrator/release_flow.py",
    "orchestrator/submit_task.py",
    "orchestrator/codex_runner.py",
    "orchestrator/codex_stage01b_runner.py",
    "orchestrator/control_loop.py",
    "scripts/activate_repair_team_v1.py",
}


class RepairBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class BridgeResult:
    incident_id: str
    status: str
    task_id: str
    path: str


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise RepairBridgeError(f"state directory symlink rejected: {path.parent}")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> tuple[dict, str]:
    if path.is_symlink():
        raise RepairBridgeError(f"symlink diagnosis result rejected: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RepairBridgeError(f"cannot read diagnosis result: {exc}") from exc
    if not raw or len(raw) > RESULT_LIMIT:
        raise RepairBridgeError("diagnosis result size out of bounds")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RepairBridgeError(f"invalid diagnosis result JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RepairBridgeError("diagnosis result must be an object")
    return payload, hashlib.sha256(raw).hexdigest()


def _open_incidents(state_root: Path) -> dict[str, dict]:
    return {
        item["incident_id"]: item
        for item in incident_summary(state_root).get("open_incidents", [])
        if isinstance(item, dict) and isinstance(item.get("incident_id"), str)
    }


def _safe_one_line(value: str, limit: int) -> str:
    text = " ".join(str(value).replace("\x00", " ").split())
    if not text:
        raise RepairBridgeError("empty generated task text")
    return text[:limit]


def _task_id(project_id: str, incident_id: str) -> str:
    project = re.sub(r"[^A-Z0-9_]", "_", project_id.upper())
    suffix = incident_id.rsplit("-", 1)[-1]
    value = f"REPAIR_{project}_{suffix}"
    if not TASK_ID_RE.fullmatch(value):
        raise RepairBridgeError("generated task id is invalid")
    return value


def _work_branch(incident_id: str) -> str:
    return f"fix/repair-{incident_id.lower()}"


def _automatic_repair_scope_allowed(source: str) -> bool:
    """Return whether model evidence may authorize an incident-origin code edit."""
    path = PurePosixPath(source)
    normalized = path.as_posix()
    if normalized in AUTOMATIC_REPAIR_DENY_EXACT:
        return False
    if any(normalized.startswith(prefix) for prefix in AUTOMATIC_REPAIR_DENY_PREFIXES):
        return False
    name = path.name.lower()
    if name.startswith("tsconfig") and name.endswith(".json"):
        return False
    if name.startswith("eslint") or name.startswith("jest") or name.startswith("vitest"):
        return False
    return True


def _validate_result(root: Path, state_root: Path, payload: dict) -> tuple[dict, str, list[str]]:
    required = {
        "version",
        "diagnosed_at",
        "project_id",
        "probe_id",
        "response_class",
        "effective_next_action",
        "eligible_runbooks",
        "diagnosis",
    }
    if set(payload) != required or payload.get("version") != diagnosis_runner.RESULT_VERSION:
        raise RepairBridgeError("diagnosis result schema mismatch")
    project_id = payload.get("project_id")
    probe_id = payload.get("probe_id")
    if not isinstance(project_id, str) or not isinstance(probe_id, str):
        raise RepairBridgeError("diagnosis project/probe binding invalid")
    diagnosis = payload.get("diagnosis")
    if not isinstance(diagnosis, dict):
        raise RepairBridgeError("diagnosis body missing")
    incident_id = diagnosis.get("incident_id")
    if not isinstance(incident_id, str) or not INCIDENT_ID_RE.fullmatch(incident_id):
        raise RepairBridgeError("diagnosis incident id invalid")
    current_incident = _open_incidents(state_root).get(incident_id)
    if current_incident is None:
        raise RepairBridgeError("incident is no longer open")
    if current_incident.get("project_id") != project_id or current_incident.get("probe_id") != probe_id:
        raise RepairBridgeError("diagnosis result incident binding mismatch")

    current_class = classify(root, project_id, probe_id)
    if payload.get("response_class") != current_class:
        raise RepairBridgeError("diagnosis response class is stale or tampered")
    parsed_diagnosis = diagnosis_runner.parse_diagnosis_output(
        json.dumps(diagnosis, ensure_ascii=False), incident_id
    )
    expected_action, expected_runbooks = diagnosis_runner.deterministic_next_action(
        root,
        {"project_id": project_id, "probe_id": probe_id, "response_class": current_class},
        parsed_diagnosis,
        current_class,
    )
    listed_runbooks = payload.get("eligible_runbooks")
    if not isinstance(listed_runbooks, list) or not all(isinstance(x, str) for x in listed_runbooks):
        raise RepairBridgeError("eligible runbooks invalid")
    if payload.get("effective_next_action") != expected_action or sorted(listed_runbooks) != sorted(expected_runbooks):
        raise RepairBridgeError("diagnosis effective action is stale or tampered")
    if expected_action not in REPAIR_ACTIONS:
        raise RepairBridgeError(f"diagnosis is not eligible for code repair task: {expected_action}")
    if parsed_diagnosis.get("suggested_action") != "CODE_REPAIR":
        raise RepairBridgeError("non-code diagnosis must use a registered operations path")
    return parsed_diagnosis, current_class, expected_runbooks


def _scope_from_evidence(project: dict, diagnosis: dict) -> list[str]:
    project_path = Path(project["path"])
    candidates: list[str] = []
    for item in diagnosis.get("evidence", []):
        if not isinstance(item, dict):
            continue
        raw = item.get("source")
        if not isinstance(raw, str):
            continue
        source = raw.strip()
        if not source or "\\" in source or source.startswith("/"):
            continue
        if not _automatic_repair_scope_allowed(source):
            continue
        candidate = project_path / source
        try:
            candidate.lstat()
        except OSError:
            continue
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            validated = submit_task.validate_scope_path(
                project_path, source, project["allowed_scope"]
            )
        except submit_task.IntakeError:
            continue
        candidates.append(validated)
    scope = sorted(set(candidates))
    if not scope:
        raise RepairBridgeError("diagnosis contains no existing safe allowlisted code evidence path")
    if len(scope) > int(project.get("max_scope_files", submit_task.SCOPE_COUNT_LIMIT)):
        raise RepairBridgeError("diagnosis scope exceeds project max_scope_files")
    return submit_task.validate_scope(project_path, scope, project["allowed_scope"])


def _existing_task(runtime: Path, task_id: str) -> tuple[str, Path] | None:
    try:
        return submit_task.locate_task(runtime, task_id)
    except submit_task.IntakeError as exc:
        if "task not found" in str(exc):
            return None
        raise


def _write_bridge_record(
    state_root: Path,
    incident_id: str,
    task_id: str,
    diagnosis_sha256: str,
    scope: list[str],
    work_branch: str,
    response_class: str,
    runbooks: list[str],
    test_contract: dict,
) -> Path:
    destination = state_root / "repair_bridge" / "tasks" / f"{incident_id}.json"
    payload = {
        "version": BRIDGE_VERSION,
        "incident_id": incident_id,
        "task_id": task_id,
        "diagnosis_sha256": diagnosis_sha256,
        "scope": scope,
        "work_branch": work_branch,
        "response_class": response_class,
        "eligible_runbooks": sorted(runbooks),
        "test_contract_id": test_contract["contract_id"],
        "test_contract_sha256": test_contract["sha256"],
        "test_contract_outcome": test_contract["required_outcome"],
    }
    _atomic_write(destination, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return destination


def _write_blocked(state_root: Path, incident_id: str, error: Exception) -> Path:
    destination = state_root / "repair_bridge" / "blocked" / f"{incident_id}.json"
    payload = {
        "version": BRIDGE_VERSION,
        "incident_id": incident_id,
        "error_type": type(error).__name__,
        "error": _safe_one_line(str(error), 1000),
    }
    _atomic_write(destination, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return destination


def bridge_result(root: Path, state_root: Path, result_path: Path) -> BridgeResult:
    fallback_id = result_path.stem if INCIDENT_ID_RE.fullmatch(result_path.stem) else "INVALID"
    try:
        payload, diagnosis_sha256 = _read_json(result_path)
        diagnosis, response_class, runbooks = _validate_result(root, state_root, payload)
        incident_id = diagnosis["incident_id"]
        projects = submit_task.read_registry(root)
        project_id = payload["project_id"]
        if project_id not in projects:
            raise RepairBridgeError("diagnosis project is not registered for task intake")
        project = projects[project_id]
        test_contract = contract_for_project(root, project_id)
        scope = _scope_from_evidence(project, diagnosis)
        task_id = _task_id(project_id, incident_id)
        work_branch = _work_branch(incident_id)
        if not submit_task.WORK_BRANCH_RE.fullmatch(work_branch) or not any(
            work_branch.startswith(prefix) for prefix in project["work_prefixes"]
        ):
            raise RepairBridgeError("generated repair branch is outside project allowlist")

        title = _safe_one_line(f"Repair {project_id} incident {incident_id}", submit_task.TITLE_LIMIT)
        root_cause = _safe_one_line(diagnosis["root_cause"], 1800)
        instructions = _safe_one_line(
            f"Repair incident {incident_id}. Validated root cause: {root_cause}. "
            "Make the minimum safe code change inside Scope-Files only. Preserve business rules, "
            "prices, legal rules, credentials, permissions, production data and unrelated behavior. "
            "Do not modify tests, test/toolchain configuration, scripts, migrations, deployment surfaces, "
            "or Repair Team authority files. Run every Required-Check and leave production deployment out of scope.",
            submit_task.INSTRUCTION_LIMIT,
        )
        content = submit_task.render_task(
            project,
            task_id,
            title,
            instructions,
            work_branch,
            scope,
            metadata=[
                ("Repair-Origin", "incident"),
                ("Incident-ID", incident_id),
                ("Diagnosis-SHA256", diagnosis_sha256),
                ("Repair-Response-Class", response_class),
                ("Repair-Runbook-IDs", ",".join(sorted(runbooks)) if runbooks else "none"),
                ("Test-Contract-ID", test_contract["contract_id"]),
                ("Test-Contract-SHA256", test_contract["sha256"]),
                ("Test-Contract-Outcome", test_contract["required_outcome"]),
            ],
        )

        runtime = initialize(state_root)
        existing = _existing_task(runtime, task_id)
        if existing is not None:
            queue, existing_path = existing
            existing_text = existing_path.read_text(encoding="utf-8")
            required_markers = [
                f"Incident-ID: {incident_id}",
                f"Diagnosis-SHA256: {diagnosis_sha256}",
                f"Work-Branch: {work_branch}",
                f"Test-Contract-ID: {test_contract['contract_id']}",
                f"Test-Contract-SHA256: {test_contract['sha256']}",
            ]
            if not all(marker in existing_text for marker in required_markers):
                raise RepairBridgeError("deterministic repair task id conflicts with different task")
            _write_bridge_record(
                state_root,
                incident_id,
                task_id,
                diagnosis_sha256,
                scope,
                work_branch,
                response_class,
                runbooks,
                test_contract,
            )
            return BridgeResult(incident_id, f"already_{queue}", task_id, str(existing_path))

        destination = runtime / "queue" / "pending" / f"{task_id}.md"
        submit_task.atomic_create(destination, content)
        _write_bridge_record(
            state_root,
            incident_id,
            task_id,
            diagnosis_sha256,
            scope,
            work_branch,
            response_class,
            runbooks,
            test_contract,
        )
        return BridgeResult(incident_id, "created", task_id, str(destination))
    except Exception as exc:
        blocked = _write_blocked(state_root, fallback_id, exc)
        return BridgeResult(fallback_id, "blocked", "", str(blocked))


def process_once(root: Path, state_root: Path) -> BridgeResult | None:
    results = state_root / "diagnosis" / "results"
    if not results.is_dir():
        return None
    if results.is_symlink():
        raise RepairBridgeError(f"diagnosis results directory symlink rejected: {results}")
    for path in sorted(results.glob("INC-*.json")):
        incident_id = path.stem
        record = state_root / "repair_bridge" / "tasks" / f"{incident_id}.json"
        blocked = state_root / "repair_bridge" / "blocked" / f"{incident_id}.json"
        if record.exists() or blocked.exists():
            continue
        return bridge_result(root, state_root, path)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = process_once(args.root, args.state_root)
    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "blocked", "error": _safe_one_line(str(exc), 1000)}, sort_keys=True))
        else:
            print(f"BLOCKED {_safe_one_line(str(exc), 1000)}")
        return 1
    payload = result.__dict__ if result else {"status": "idle"}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif result:
        print(f"{result.status.upper()} incident={result.incident_id} task={result.task_id} path={result.path}")
    else:
        print("IDLE")
    return 1 if result and result.status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
