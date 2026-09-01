"""Bounded read-only correlation evidence for simultaneously open incidents.

Correlation here is intentionally non-causal: it only reports other incidents
for the same project that are open at packet-generation time. It never merges,
suppresses, resolves, repairs, reprioritizes, or assigns a shared root cause.
"""
from __future__ import annotations

from typing import Iterable

CORRELATION_VERSION = 1
MAX_CORRELATED_PEERS = 16


class IncidentCorrelationError(ValueError):
    pass


def _required_string(item: dict, field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise IncidentCorrelationError(f"incident correlation field missing: {field}")
    return value


def build_correlation_view(current: dict, open_incidents: Iterable[dict]) -> dict:
    """Return deterministic, bounded same-project peer evidence."""
    if not isinstance(current, dict):
        raise IncidentCorrelationError("current incident must be an object")
    current_id = _required_string(current, "incident_id")
    project_id = _required_string(current, "project_id")

    peers: list[dict] = []
    for item in open_incidents:
        if not isinstance(item, dict):
            raise IncidentCorrelationError("open incident entry must be an object")
        incident_id = _required_string(item, "incident_id")
        item_project = _required_string(item, "project_id")
        status = _required_string(item, "status")
        if status != "open" or incident_id == current_id or item_project != project_id:
            continue
        peers.append(
            {
                "incident_id": incident_id,
                "probe_id": _required_string(item, "probe_id"),
                "severity": _required_string(item, "severity"),
                "opened_at": _required_string(item, "opened_at"),
                "updated_at": _required_string(item, "updated_at"),
                "last_observation_at": _required_string(item, "last_observation_at"),
            }
        )

    peers.sort(key=lambda item: item["incident_id"])
    total = len(peers)
    included = peers[:MAX_CORRELATED_PEERS]
    return {
        "version": CORRELATION_VERSION,
        "basis": "same_project_open_incidents",
        "causal_inference": False,
        "total_peer_count": total,
        "included_peer_count": len(included),
        "truncated": total > len(included),
        "peers": included,
    }
