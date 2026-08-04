from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from orchestrator import control_loop


class TelegramBridgeSupervisorTests(unittest.TestCase):
    def make_paths(self, source: str):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)

        root = Path(temporary.name)
        orchestrator = root / "orchestrator"
        orchestrator.mkdir(parents=True)

        bridge = orchestrator / "telegram_bridge.py"
        bridge.write_text(source, encoding="utf-8")

        paths = control_loop.build_paths(
            root,
            root / "runtime",
        )

        return root, paths

    def test_bridge_starts_and_stops(self):
        _root, paths = self.make_paths(
            "import time\n"
            "time.sleep(30)\n"
        )

        process = control_loop.start_telegram_bridge(
            paths
        )

        try:
            self.assertIsNone(process.poll())
            self.assertEqual(
                control_loop.telegram_bridge_pid_path(
                    paths
                ).read_text(
                    encoding="utf-8"
                ).strip(),
                str(process.pid),
            )
        finally:
            control_loop.stop_telegram_bridge(
                paths,
                process,
            )

        self.assertIsNotNone(process.poll())
        self.assertFalse(
            control_loop.telegram_bridge_pid_path(
                paths
            ).exists()
        )

    def test_supervisor_restarts_failed_bridge(self):
        _root, paths = self.make_paths(
            "from pathlib import Path\n"
            "import time\n"
            "counter = Path(__file__).with_name('runs')\n"
            "runs = int(counter.read_text()) if counter.exists() else 0\n"
            "counter.write_text(str(runs + 1))\n"
            "if runs == 0:\n"
            "    raise SystemExit(7)\n"
            "time.sleep(30)\n"
        )

        stop_event = threading.Event()
        thread = threading.Thread(
            target=control_loop.supervise_telegram_bridge,
            args=(paths, stop_event),
            kwargs={
                "poll_interval": 0.05,
                "restart_delay": 0.05,
            },
        )
        thread.start()

        runs_path = (
            paths.root
            / "orchestrator"
            / "runs"
        )

        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:
            if (
                runs_path.exists()
                and int(runs_path.read_text()) >= 2
            ):
                break
            time.sleep(0.05)
        else:
            self.fail(
                "Telegram Bridge was not restarted"
            )

        stop_event.set()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertFalse(
            control_loop.telegram_bridge_pid_path(
                paths
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
