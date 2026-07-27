"""Immutable allowlist for privileged, non-interactive production operations."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OperationProfile:
    key: str
    repository: Path
    base_branch: str
    expected_migration: str


AK_BERMET_SUPABASE_RPC_DEPLOY = OperationProfile(
    key="ak-bermet-supabase-rpc-deploy",
    repository=Path("/home/agent/projects/ak-bermet"),
    base_branch="develop",
    expected_migration=(
        "supabase/migrations/"
        "20260727000100_manager_inspection_blocking_problem.sql"
    ),
)

PROFILES = {
    AK_BERMET_SUPABASE_RPC_DEPLOY.key: AK_BERMET_SUPABASE_RPC_DEPLOY,
}


def resolve_profile(key: str) -> OperationProfile:
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise ValueError(f"unknown operation profile: {key}") from exc
