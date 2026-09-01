#!/usr/bin/env python3
"""Diagnosis packet canary that embeds non-causal incident correlation evidence.

The baseline diagnosis packet schema and authority flags are preserved. This
wrapper only enriches the existing nested incident evidence with bounded peers
that are concurrently open for the same project.
"""
from __future__ import annotations

try:  # Package import under unit tests.
    from orchestrator import diagnosis_packet as base
    from orchestrator.incident_correlation import build_correlation_view
except ImportError:  # Direct script execution from orchestrator/.
    import diagnosis_packet as base  # type: ignore[no-redef]
    from incident_correlation import build_correlation_view  # type: ignore[no-redef]

_ORIGINAL_BUILD_PACKET = base.build_packet


def build_packet(root, state_root, incident):
    packet = _ORIGINAL_BUILD_PACKET(root, state_root, incident)
    current = base.incident_summary(state_root)
    open_incidents = current.get("open_incidents", [])
    if not isinstance(open_incidents, list):
        raise base.DiagnosisPacketError("incident summary open_incidents must be a list")
    envelope = packet.get("incident")
    if not isinstance(envelope, dict):
        raise base.DiagnosisPacketError("incident evidence envelope missing")
    envelope["correlation"] = build_correlation_view(incident, open_incidents)
    return packet


def main() -> int:
    original = base.build_packet
    try:
        base.build_packet = build_packet
        return base.main()
    finally:
        base.build_packet = original


if __name__ == "__main__":
    raise SystemExit(main())
