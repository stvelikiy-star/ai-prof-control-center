"""Keep top-level unittest discovery deterministic.

`python3 -m unittest` is the exact self-maintenance gate. The repository's
legacy tests intentionally manipulate import paths and module names, so loading
them all into one interpreter is unsafe and historically produced false import
collisions. Top-level discovery therefore runs only `test_control_center.py`;
that protected aggregator executes every `tests/test_*.py` file in its own
Python process.
"""
from __future__ import annotations

import unittest


def load_tests(loader, standard_tests, pattern):
    del loader, standard_tests, pattern
    return unittest.TestSuite()
