"""Immutable allowlist for privileged and release-control operations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OperationProfile:
    key: str
    repository: Path
    base_branch: str
    expected_migration: str
    kind: str = "migration"


AI_PROF_CONTROL_CENTER_HEALTH_CHECK = OperationProfile(
    key="ai-prof-control-center-health-check",
    repository=Path("/home/agent/projects/ai-prof-control-center-maintenance"),
    base_branch="maintenance/base",
    expected_migration="",
    kind="control-center-health-check",
)


KOL_READONLY_FULL_AUDIT = OperationProfile(
    key="kol-readonly-full-audit",
    repository=Path("/home/agent/Загрузки/kol-travel-platform"),
    base_branch="feature/kol-ai-prof-pipeline-20261109",
    expected_migration="",
    kind="kol-readonly-full-audit",
)


PROFILES = {
    AI_PROF_CONTROL_CENTER_HEALTH_CHECK.key: AI_PROF_CONTROL_CENTER_HEALTH_CHECK,
    KOL_READONLY_FULL_AUDIT.key: KOL_READONLY_FULL_AUDIT,
}


def resolve_profile(key: str) -> OperationProfile:
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unknown operation profile: {key}") from exc
