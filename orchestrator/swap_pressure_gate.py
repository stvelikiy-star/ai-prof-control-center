#!/usr/bin/env python3
"""Swap severity based on capacity AND live memory pressure."""
from __future__ import annotations
import argparse
from pathlib import Path

def psi_some_avg60(path: Path) -> float | None:
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            if line.startswith("some "):
                for item in line.split()[1:]:
                    if item.startswith("avg60="):
                        return float(item.removeprefix("avg60="))
    except (OSError, ValueError, UnicodeError):
        return None
    return None

def severity(swap_pct: int, ram_available_pct: int, psi: float | None) -> str:
    if not 0 <= swap_pct <= 100 or not 0 <= ram_available_pct <= 100:
        return "CRITICAL"
    if swap_pct >= 80 and (ram_available_pct <= 15 or psi is None or psi >= 5):
        return "CRITICAL"
    if swap_pct >= 50:
        return "WARNING"
    return "OK"

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("swap_pct", type=int)
    parser.add_argument("ram_available_pct", type=int)
    args=parser.parse_args()
    print(severity(args.swap_pct, args.ram_available_pct,
                   psi_some_avg60(Path("/proc/pressure/memory"))))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
