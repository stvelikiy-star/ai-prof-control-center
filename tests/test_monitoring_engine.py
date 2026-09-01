from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
sys.path.insert(0, str(ORCHESTRATOR))

import monitoring_engine as monitor


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return


class MonitoringEngineTests(unittest.TestCase):
    def _root_with_project(self, project: dict) -> Path:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "orchestrator").mkdir()
        payload = {"version": 1, "projects": [project]}
        (root / "orchestrator" / "projects.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        return root

    def _project(self, path: Path, probes: list[dict]) -> dict:
        return {
            "project_id": "demo",
            "path": str(path),
            "base_branch": "main",
            "allowed_base_branches": ["main"],
            "enabled": True,
            "monitoring": {"enabled": True, "probes": probes},
        }

    def test_path_exists_probe(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            path = Path(project_tmp)
            root = self._root_with_project(
                self._project(path, [{"id": "runtime", "kind": "path_exists", "path": str(path)}])
            )
            observations = monitor.monitor_projects(root)
            self.assertEqual(len(observations), 1)
            self.assertTrue(observations[0].ok)
            self.assertEqual(observations[0].fingerprint, "demo:runtime")

    def test_missing_path_is_failure(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            project_path = Path(project_tmp)
            missing = project_path / "missing"
            root = self._root_with_project(
                self._project(project_path, [{"id": "missing", "kind": "path_exists", "path": str(missing)}])
            )
            self.assertFalse(monitor.monitor_projects(root)[0].ok)

    def test_fresh_and_stale_heartbeat(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            project_path = Path(project_tmp)
            heartbeat = project_path / "heartbeat.json"
            probe = {
                "id": "heartbeat",
                "kind": "heartbeat_json",
                "path": str(heartbeat),
                "max_age_seconds": 30,
            }
            root = self._root_with_project(self._project(project_path, [probe]))
            heartbeat.write_text(
                json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "state": "idle"}),
                encoding="utf-8",
            )
            self.assertTrue(monitor.monitor_projects(root)[0].ok)
            heartbeat.write_text(
                json.dumps({
                    "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                    "state": "idle",
                }),
                encoding="utf-8",
            )
            self.assertFalse(monitor.monitor_projects(root)[0].ok)

    def test_http_get_probe(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        port = server.server_address[1]
        with tempfile.TemporaryDirectory() as project_tmp:
            project_path = Path(project_tmp)
            root = self._root_with_project(
                self._project(project_path, [{
                    "id": "http",
                    "kind": "http_get",
                    "url": f"http://127.0.0.1:{port}/health",
                    "expected_status": 200,
                }])
            )
            self.assertTrue(monitor.monitor_projects(root)[0].ok)

    def test_tcp_probe(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory() as project_tmp:
            project_path = Path(project_tmp)
            root = self._root_with_project(
                self._project(project_path, [{
                    "id": "tcp",
                    "kind": "tcp_connect",
                    "host": "127.0.0.1",
                    "port": port,
                }])
            )
            self.assertTrue(monitor.monitor_projects(root)[0].ok)

    def test_git_clean_probe_and_dirty_detection(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            project_path = Path(project_tmp)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project_path, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=project_path, check=True)
            subprocess.run(["git", "config", "user.name", "test"], cwd=project_path, check=True)
            (project_path / "README.md").write_text("ok\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=project_path, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=project_path, check=True)
            root = self._root_with_project(
                self._project(project_path, [{"id": "git", "kind": "git_clean", "path": str(project_path)}])
            )
            self.assertTrue(monitor.monitor_projects(root)[0].ok)
            (project_path / "README.md").write_text("dirty\n", encoding="utf-8")
            self.assertFalse(monitor.monitor_projects(root)[0].ok)

    def test_arbitrary_probe_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as project_tmp:
            project_path = Path(project_tmp)
            root = self._root_with_project(
                self._project(project_path, [{"id": "bad", "kind": "shell", "command": "rm -rf /"}])
            )
            with self.assertRaises(monitor.MonitoringConfigError):
                monitor.monitor_projects(root)

    def test_snapshot_is_atomic_json(self):
        with tempfile.TemporaryDirectory() as state_tmp:
            observation = monitor.Observation(
                project_id="demo",
                probe_id="p",
                kind="path_exists",
                severity="warning",
                ok=True,
                checked_at=monitor.utc_now(),
                latency_ms=1,
                detail="ok",
                fingerprint="demo:p",
            )
            path = monitor.write_snapshot(Path(state_tmp), [observation])
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)
            self.assertEqual(payload["observations"][0]["fingerprint"], "demo:p")


if __name__ == "__main__":
    unittest.main()
