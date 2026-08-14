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


AK_BERMET_SUPABASE_RPC_DEPLOY = OperationProfile(
    key="ak-bermet-supabase-rpc-deploy",
    repository=Path("/home/agent/projects/ak-bermet"),
    base_branch="develop",
    expected_migration=(
        "supabase/migrations/"
        "20260727000100_manager_inspection_blocking_problem.sql"
    ),
)

AK_BERMET_PRODUCTION_PREPARE_V6 = OperationProfile(
    key="ak-bermet-production-prepare-v6",
    repository=Path("/home/agent/projects/ak-bermet"),
    base_branch="main",
    expected_migration="",
    kind="release-v6-prepare",
)

AK_BERMET_SHEETS_MIRROR_DRY_RUN = OperationProfile(
    key="ak-bermet-sheets-mirror-dry-run",
    repository=Path("/home/agent/projects/ak-bermet"),
    base_branch="main",
    expected_migration="",
    kind="sheets-mirror-dry-run",
)

PROFILES = {
    AK_BERMET_SUPABASE_RPC_DEPLOY.key: AK_BERMET_SUPABASE_RPC_DEPLOY,
    AK_BERMET_PRODUCTION_PREPARE_V6.key: AK_BERMET_PRODUCTION_PREPARE_V6,
    AK_BERMET_SHEETS_MIRROR_DRY_RUN.key: AK_BERMET_SHEETS_MIRROR_DRY_RUN,
}


def resolve_profile(key: str) -> OperationProfile:
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unknown operation profile: {key}") from exc
