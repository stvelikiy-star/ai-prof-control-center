#!/usr/bin/env python3
"""Read-only multi-project monitoring for AI PROF Repair Team.

The engine intentionally supports only fixed probe kinds. Project profile text
can select bounded parameters, but can never provide arbitrary shell commands,
arbitrary filesystem probes, credentials in URLs, or redirect-based network
pivots. Failures are observations; mutations are delegated to the incident and
repair pipeline and are never performed by this module.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from monitoring_profiles import MonitoringProfileError, load_monitoring_profiles
from project_registry import ProjectPolicyError, load_projects, project_enabled

DEFAULT_ROOT = Path("/home/agent/projects/ai-prof-control-center")
DEFAULT_STATE_ROOT = Path("/home/agent/.local/state/ai-prof-control-center")
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_HTTP_BODY_BYTES = 4096
ALLOWED_SEVERITIES = {"info", "warning", "critical"}
ALLOWED_PROBE_KINDS = {
    "path_exists",
    "heartbeat_json",
    "http_get",
    "tcp_connect",
    "git_clean",
}
CONTROL_CENTER_PROJECT_ID = "ai-prof-control-center"
CONTROL_CENTER_RUNTIME = Path("/home/agent/projects/ai-prof-control-center")
CONTROL_CENTER_STATE = Path("/home/agent/.local/state/ai-prof-control-center")
BLOCKED_NETWORK_HOSTS = {"metadata.google.internal"}


class MonitoringConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Observation:
    project_id: str
    probe_id: str
    kind: str
    severity: str
    ok: bool
    checked_at: str
    latency_ms: int
    detail: str
    fingerprint: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_text(value: Any, limit: int = 1000) -> str:
    text = str(value).replace("\x00", "")
    return text if len(text) <= limit else text[:limit] + "…"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _require_str(mapping: dict, key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MonitoringConfigError(f"invalid {key}")
    return value


def _monitoring_config(project: dict) -> dict:
    raw = project.get("monitoring", {"enabled": False, "probes": []})
    if not isinstance(raw, dict):
        raise MonitoringConfigError("monitoring must be an object")
    enabled = raw.get("enabled", False)
    probes = raw.get("probes", [])
    if not isinstance(enabled, bool):
        raise MonitoringConfigError("monitoring.enabled must be boolean")
    if not isinstance(probes, list):
        raise MonitoringConfigError("monitoring.probes must be a list")
    if enabled and not probes:
        raise MonitoringConfigError("enabled monitoring requires probes")
    return {"enabled": enabled, "probes": probes}


def _path_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _allowed_path_roots(project: dict) -> tuple[Path, ...]:
    registered = Path(_require_str(project, "path")).resolve(strict=False)
    roots = [registered]
    if project.get("project_id") == CONTROL_CENTER_PROJECT_ID:
        roots.extend(
            [
                CONTROL_CENTER_RUNTIME.resolve(strict=False),
                CONTROL_CENTER_STATE.resolve(strict=False),
            ]
        )
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return tuple(unique)


def _validate_monitored_path(project: dict, probe_id: str, raw_path: str) -> str:
    path = Path(raw_path)
    if not path.is_absolute():
        raise MonitoringConfigError(f"{probe_id} path must be absolute")
    resolved = path.resolve(strict=False)
    roots = _allowed_path_roots(project)
    if not any(resolved == root or _path_within(resolved, root) for root in roots):
        raise MonitoringConfigError(f"{probe_id} path is outside approved project/state roots")
    return str(resolved)


def _validate_network_host(probe_id: str, host: str) -> str:
    normalized = host.strip().lower().rstrip(".")
    if not normalized or normalized in BLOCKED_NETWORK_HOSTS:
        raise MonitoringConfigError(f"blocked network host for {probe_id}")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return host
    if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
        raise MonitoringConfigError(f"blocked network address for {probe_id}")
    return host


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_http_url(probe_id: str, url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise MonitoringConfigError(f"invalid URL for {probe_id}: {exc}") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MonitoringConfigError(f"{probe_id} URL must be http(s)")
    if parsed.username is not None or parsed.password is not None:
        raise MonitoringConfigError(f"{probe_id} URL credentials are forbidden")
    if parsed.fragment:
        raise MonitoringConfigError(f"{probe_id} URL fragments are forbidden")
    _validate_network_host(probe_id, parsed.hostname)
    if parsed.scheme == "http" and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise MonitoringConfigError(f"{probe_id} non-local HTTP is forbidden; use HTTPS")
    return url


def validate_probe(project: dict, probe: dict) -> dict:
    if not isinstance(probe, dict):
        raise MonitoringConfigError("probe must be an object")
    probe_id = _require_str(probe, "id")
    kind = _require_str(probe, "kind")
    if kind not in ALLOWED_PROBE_KINDS:
        raise MonitoringConfigError(f"unsupported probe kind: {kind}")
    severity = probe.get("severity", "warning")
    if severity not in ALLOWED_SEVERITIES:
        raise MonitoringConfigError(f"invalid severity for {probe_id}")
    timeout = probe.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not (0 < timeout <= 30):
        raise MonitoringConfigError(f"invalid timeout for {probe_id}")

    normalized = dict(probe)
    normalized["id"] = probe_id
    normalized["kind"] = kind
    normalized["severity"] = severity
    normalized["timeout_seconds"] = float(timeout)

    if kind in {"path_exists", "heartbeat_json"}:
        normalized["path"] = _validate_monitored_path(
            project, probe_id, _require_str(probe, "path")
        )
    elif kind == "http_get":
        normalized["url"] = _validate_http_url(probe_id, _require_str(probe, "url"))
        expected = probe.get("expected_status", 200)
        if isinstance(expected, bool) or not isinstance(expected, int) or not (100 <= expected <= 599):
            raise MonitoringConfigError(f"invalid expected_status for {probe_id}")
        normalized["expected_status"] = expected
    elif kind == "tcp_connect":
        normalized["host"] = _validate_network_host(probe_id, _require_str(probe, "host"))
        port = probe.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
            raise MonitoringConfigError(f"invalid port for {probe_id}")
        normalized["port"] = port
    elif kind == "git_clean":
        project_path = Path(_require_str(project, "path")).resolve(strict=False)
        requested = Path(str(probe.get("path", project_path))).resolve(strict=False)
        if requested != project_path:
            raise MonitoringConfigError(f"{probe_id} git path must equal registered project path")
        normalized["path"] = str(requested)

    if kind == "heartbeat_json":
        max_age = probe.get("max_age_seconds", 60)
        if isinstance(max_age, bool) or not isinstance(max_age, (int, float)) or not (1 <= max_age <= 86400):
            raise MonitoringConfigError(f"invalid max_age_seconds for {probe_id}")
        normalized["max_age_seconds"] = float(max_age)
    return normalized


def validate_project_monitoring(project: dict) -> list[dict]:
    config = _monitoring_config(project)
    if not config["enabled"]:
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for probe in config["probes"]:
        normalized = validate_probe(project, probe)
        if normalized["id"] in seen:
            raise MonitoringConfigError(f"duplicate probe id: {normalized['id']}")
        seen.add(normalized["id"])
        result.append(normalized)
    return result


def _path_exists(probe: dict) -> tuple[bool, str]:
    path = Path(probe["path"])
    exists = path.exists()
    return exists, f"path={path} exists={exists}"


def _heartbeat_json(probe: dict) -> tuple[bool, str]:
    path = Path(probe["path"])
    if not path.is_file():
        return False, f"heartbeat missing: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"heartbeat invalid: {_bounded_text(exc)}"
    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, str):
        return False, "heartbeat timestamp missing"
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return False, "heartbeat timestamp invalid"
    max_age = probe["max_age_seconds"]
    ok = age <= max_age
    state = _bounded_text(payload.get("state", "unknown"), 80)
    return ok, f"age_seconds={age:.1f} max_age_seconds={max_age:.1f} state={state}"


def _http_get(probe: dict) -> tuple[bool, str]:
    request = urllib.request.Request(
        probe["url"],
        method="GET",
        headers={"User-Agent": "AI-PROF-Monitor/1"},
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=probe["timeout_seconds"]) as response:
            body = response.read(MAX_HTTP_BODY_BYTES)
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        return False, f"http_status={exc.code} expected={probe['expected_status']}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"http_error={_bounded_text(exc)}"
    ok = status == probe["expected_status"]
    return ok, f"http_status={status} expected={probe['expected_status']} bytes={len(body)}"


def _tcp_connect(probe: dict) -> tuple[bool, str]:
    try:
        with socket.create_connection(
            (probe["host"], probe["port"]), timeout=probe["timeout_seconds"]
        ):
            pass
    except OSError as exc:
        return False, f"tcp_error={_bounded_text(exc)}"
    return True, f"tcp={probe['host']}:{probe['port']} connected"


def _git_clean(probe: dict) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", "-C", probe["path"], "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=probe["timeout_seconds"],
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"git_error={_bounded_text(exc)}"
    if result.returncode != 0:
        return False, f"git_status_failed rc={result.returncode} stderr={_bounded_text(result.stderr)}"
    dirty = bool(result.stdout.strip())
    return not dirty, "git_clean=true" if not dirty else "git_clean=false"


PROBE_RUNNERS = {
    "path_exists": _path_exists,
    "heartbeat_json": _heartbeat_json,
    "http_get": _http_get,
    "tcp_connect": _tcp_connect,
    "git_clean": _git_clean,
}


def run_probe(project_id: str, probe: dict) -> Observation:
    started = time.monotonic()
    try:
        ok, detail = PROBE_RUNNERS[probe["kind"]](probe)
    except Exception as exc:
        ok, detail = False, f"probe_exception={type(exc).__name__}:{_bounded_text(exc)}"
    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    return Observation(
        project_id=project_id,
        probe_id=probe["id"],
        kind=probe["kind"],
        severity=probe["severity"],
        ok=ok,
        checked_at=utc_now(),
        latency_ms=latency_ms,
        detail=_bounded_text(detail),
        fingerprint=f"{project_id}:{probe['id']}",
    )


def monitor_projects(root: Path, only_project: str | None = None) -> list[Observation]:
    projects = load_projects(root)
    try:
        profiles = load_monitoring_profiles(root, set(projects))
    except MonitoringProfileError as exc:
        raise MonitoringConfigError(str(exc)) from exc
    if only_project is not None and only_project not in projects:
        raise MonitoringConfigError(f"unknown project: {only_project}")
    observations: list[Observation] = []
    for project_id, project in projects.items():
        if only_project is not None and project_id != only_project:
            continue
        if not project_enabled(project):
            continue
        effective = dict(project)
        effective["project_id"] = project_id
        if project_id in profiles:
            effective["monitoring"] = profiles[project_id]
        for probe in validate_project_monitoring(effective):
            observations.append(run_probe(project_id, probe))
    return observations


def write_snapshot(state_root: Path, observations: list[Observation]) -> Path:
    destination = state_root / "monitoring" / "latest.json"
    payload = {
        "version": 1,
        "generated_at": utc_now(),
        "observations": [asdict(item) for item in observations],
    }
    _atomic_write(destination, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    parser.add_argument("--project")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        observations = monitor_projects(args.root, args.project)
        write_snapshot(args.state_root, observations)
    except (ProjectPolicyError, MonitoringConfigError) as exc:
        print(f"MONITOR_CONFIG_ERROR: {exc}")
        return 2
    failed = [item for item in observations if not item.ok]
    if args.json:
        print(json.dumps([asdict(item) for item in observations], ensure_ascii=False, sort_keys=True))
    else:
        for item in observations:
            print(
                f"{'PASS' if item.ok else 'FAIL'} project={item.project_id} "
                f"probe={item.probe_id} severity={item.severity} detail={item.detail}"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
