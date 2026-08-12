from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"


class FullRepositorySuite(unittest.TestCase):
    def test_repository_native_suite(self):
        test_files = sorted(TESTS.glob("test_*.py"))
        self.assertTrue(test_files, "no repository tests found")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for test_file in test_files:
            result = subprocess.run(
                [sys.executable, str(test_file)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=180,
            )
            self.assertEqual(
                result.returncode,
                0,
                msg=(
                    f"{test_file.name} failed\n"
                    f"STDOUT:\n{result.stdout[-12000:]}\n"
                    f"STDERR:\n{result.stderr[-12000:]}"
                ),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
