"""Pure shadow model for the universal task lifecycle (Slices 1 and 3).

This module deliberately has no concrete adapters.  Its Slice 3 protocols are
data-only least-privilege boundaries.  It does not read or write queues,
repositories, files, databases, publishers, or runtime state.  Callers may use
the returned decisions as observations; they must not treat them as authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
import re
from typing import Iterable, Mapping, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable


class LifecycleState(str, Enum):
    """Lifecycle states, retaining the existing queue names."""

    PENDING = "pending"
    REVIEW = "review"
    PENDING_CODEX = "pending_codex"
    APPROVED = "approved"
    BLOCKED = "blocked"
    FAILED = "failed"
    TERMINAL = "terminal"


class TerminalOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class Authority(str, Enum):
    """Authority lattice.  Intersection always selects the least authority."""

    DENIED = "denied"
    OWNER_ONLY = "owner_only"
    AUTONOMOUS = "autonomous"
    # A compatibility spelling for callers which used "allowed" conceptually.
    ALLOWED = "autonomous"


class LifecycleAction(str, Enum):
    VALIDATE = "validate"
    IMPLEMENT = "implement"
    EXECUTE = "execute"
    TEST = "test"
    AUDIT = "audit"
    FIX = "fix"
    TERMINATE = "terminate"
    COMMIT = "commit"
    PUSH = "push"
    PUBLISH = "publish"
    PR = "pr"
    MERGE = "merge"


class RetryClass(str, Enum):
    NEVER = "never"
    TRANSIENT = "transient"
    FIX_LOOP = "fix_loop"
    OWNER_ACTION_REQUIRED = "owner_action_required"


class FailureKind(str, Enum):
    TRANSIENT_INFRASTRUCTURE = "transient_infrastructure"
    TIMEOUT = "timeout"
    AUDIT_REJECTED = "audit_rejected"
    CHECK_FAILED = "check_failed"
    AUTHORITY_DENIED = "authority_denied"
    OWNER_GATE = "owner_gate"
    INVALID_TASK = "invalid_task"
    POLICY_VIOLATION = "policy_violation"
    IDENTITY_DRIFT = "identity_drift"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class ReconciliationDecisionType(str, Enum):
    NO_ACTION = "no_action"
    ADVANCE_SHADOW = "advance_shadow"
    KEEP_SHADOW = "keep_shadow"
    OWNER_ACTION_REQUIRED = "owner_action_required"
    BLOCK = "block"


class LifecycleDenied(ValueError):
    """Raised when a caller tries to apply a denied shadow transition."""


class LifecycleEvidenceDenied(LifecycleDenied):
    """Raised when stage evidence is absent, stale, out of order, or mismatched."""


_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _sha(value: object, field: str, *, sha256_only: bool = False) -> str:
    text = _required_text(value, field).lower()
    matcher = _SHA256_RE if sha256_only else _SHA_RE
    if matcher.fullmatch(text) is None:
        expected = "64" if sha256_only else "40 or 64"
        raise ValueError(f"{field} must contain exactly {expected} hexadecimal characters")
    return text


@dataclass(frozen=True)
class TaskIdentity:
    """Immutable identity pinned to both the task document and source revision."""

    task_id: str
    project_id: str
    task_sha256: str
    source_sha: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _required_text(self.task_id, "task_id"))
        object.__setattr__(self, "project_id", _required_text(self.project_id, "project_id"))
        object.__setattr__(
            self, "task_sha256", _sha(self.task_sha256, "task_sha256", sha256_only=True)
        )
        object.__setattr__(self, "source_sha", _sha(self.source_sha, "source_sha"))


@dataclass(frozen=True, order=True)
class EvidenceBinding:
    """Content-addressed evidence bound to one immutable task identity."""

    kind: str
    sha256: str
    task_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _required_text(self.kind, "evidence kind"))
        object.__setattr__(self, "sha256", _sha(self.sha256, "evidence sha256", sha256_only=True))
        object.__setattr__(self, "task_id", _required_text(self.task_id, "evidence task_id"))


@dataclass(frozen=True, order=True)
class AuthorityBinding:
    action: LifecycleAction
    authority: Authority

    def __post_init__(self) -> None:
        action = _coerce_action(self.action)
        authority = _coerce_authority(self.authority)
        if action is None:
            raise ValueError("unknown lifecycle action")
        if authority is None:
            raise ValueError("unknown authority")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "authority", authority)


@dataclass(frozen=True)
class LifecycleSnapshot:
    """An immutable shadow snapshot; applying a transition returns a new one."""

    identity: TaskIdentity
    state: LifecycleState = LifecycleState.PENDING
    terminal_outcome: Optional[TerminalOutcome] = None
    authority: Tuple[AuthorityBinding, ...] = ()
    evidence: Tuple[EvidenceBinding, ...] = ()
    version: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.identity, TaskIdentity):
            raise ValueError("identity must be TaskIdentity")
        state = _coerce_state(self.state)
        if state is None:
            raise ValueError("unknown lifecycle state")
        object.__setattr__(self, "state", state)
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 0:
            raise ValueError("version must be a non-negative integer")
        outcome = _coerce_outcome(self.terminal_outcome)
        if self.terminal_outcome is not None and outcome is None:
            raise ValueError("unknown terminal outcome")
        if state is LifecycleState.TERMINAL and outcome is None:
            raise ValueError("terminal state requires terminal_outcome")
        if state is not LifecycleState.TERMINAL and outcome is not None:
            raise ValueError("terminal_outcome is valid only in terminal state")
        object.__setattr__(self, "terminal_outcome", outcome)

        bindings = tuple(self.authority)
        if any(not isinstance(item, AuthorityBinding) for item in bindings):
            raise ValueError("authority entries must be AuthorityBinding values")
        actions = [item.action for item in bindings]
        if len(actions) != len(set(actions)):
            raise ValueError("authority actions must be unique")
        object.__setattr__(self, "authority", tuple(sorted(bindings)))

        evidence = tuple(self.evidence)
        if any(not isinstance(item, EvidenceBinding) for item in evidence):
            raise ValueError("evidence entries must be EvidenceBinding values")
        if any(item.task_id != self.identity.task_id for item in evidence):
            raise ValueError("evidence must be bound to the snapshot task_id")
        object.__setattr__(self, "evidence", tuple(sorted(set(evidence))))


@dataclass(frozen=True)
class TransitionDecision:
    allowed: bool
    reason: str
    required_action: Optional[LifecycleAction]
    authority: Authority
    from_state: LifecycleState
    to_state: Optional[LifecycleState]


@dataclass(frozen=True)
class FixLoopBudget:
    max_fix_attempts: int
    max_repeated_failures: int
    fix_attempts: int = 0
    repeated_failures: int = 0
    last_failure_fingerprint: Optional[str] = None

    def __post_init__(self) -> None:
        for field in (
            "max_fix_attempts",
            "max_repeated_failures",
            "fix_attempts",
            "repeated_failures",
        ):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if self.fix_attempts > self.max_fix_attempts:
            raise ValueError("fix_attempts exceeds max_fix_attempts")
        if self.last_failure_fingerprint is not None:
            object.__setattr__(
                self,
                "last_failure_fingerprint",
                _required_text(self.last_failure_fingerprint, "last_failure_fingerprint"),
            )


@dataclass(frozen=True)
class RetryDecision:
    classification: RetryClass
    should_retry: bool
    reason: str
    budget: FixLoopBudget


@dataclass(frozen=True)
class ReconciliationDecision:
    decision: ReconciliationDecisionType
    reason: str
    target_state: Optional[LifecycleState] = None


class AdapterStage(str, Enum):
    """The only stages exposed through the Slice 3 least-privilege boundary."""

    EXECUTE = "execute"
    TEST = "test"
    AUDIT = "audit"


class EvidenceResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class OrchestrationState(str, Enum):
    """Shadow-only execution state; it is not a queue or publication state."""

    EXECUTE = "execute"
    TEST = "test"
    AUDIT = "audit"
    FIX_LOOP = "fix_loop"
    APPROVED = "approved"
    FAILED = "failed"


@dataclass(frozen=True)
class CandidateBinding:
    """A candidate pinned to its base and exactly one deterministic identity."""

    base_sha: str
    candidate_sha: Optional[str] = None
    candidate_digest: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_sha", _sha(self.base_sha, "base_sha"))
        if (self.candidate_sha is None) == (self.candidate_digest is None):
            raise ValueError(
                "exactly one of candidate_sha or candidate_digest is required"
            )
        if self.candidate_sha is not None:
            object.__setattr__(
                self, "candidate_sha", _sha(self.candidate_sha, "candidate_sha")
            )
        if self.candidate_digest is not None:
            object.__setattr__(
                self,
                "candidate_digest",
                _sha(
                    self.candidate_digest,
                    "candidate_digest",
                    sha256_only=True,
                ),
            )

    @property
    def identity(self) -> str:
        if self.candidate_sha is not None:
            return f"sha:{self.candidate_sha}"
        return f"digest:{self.candidate_digest}"


@dataclass(frozen=True)
class StageBinding:
    """Complete immutable context which every Slice 3 result must repeat."""

    task: TaskIdentity
    validated_scope_sha256: str
    candidate: CandidateBinding
    attempt: int
    policy_version: str
    evidence_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.task, TaskIdentity):
            raise ValueError("task must be TaskIdentity")
        object.__setattr__(
            self,
            "validated_scope_sha256",
            _sha(
                self.validated_scope_sha256,
                "validated_scope_sha256",
                sha256_only=True,
            ),
        )
        if not isinstance(self.candidate, CandidateBinding):
            raise ValueError("candidate must be CandidateBinding")
        if (
            not isinstance(self.attempt, int)
            or isinstance(self.attempt, bool)
            or self.attempt < 1
        ):
            raise ValueError("attempt must be a positive integer")
        object.__setattr__(
            self, "policy_version", _required_text(self.policy_version, "policy_version")
        )
        object.__setattr__(
            self,
            "evidence_version",
            _required_text(self.evidence_version, "evidence_version"),
        )

    def next_attempt(self) -> "StageBinding":
        return replace(self, attempt=self.attempt + 1)


@dataclass(frozen=True)
class StageEvidence:
    """Content-addressed PASS/FAIL evidence returned by one external stage."""

    stage: AdapterStage
    result: EvidenceResult
    binding: StageBinding
    evidence_sha256: str
    failure_fingerprint: Optional[str] = None

    def __post_init__(self) -> None:
        try:
            stage = (
                self.stage
                if isinstance(self.stage, AdapterStage)
                else AdapterStage(str(self.stage).strip().lower())
            )
            result = (
                self.result
                if isinstance(self.result, EvidenceResult)
                else EvidenceResult(str(self.result).strip().lower())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown stage evidence value") from exc
        if not isinstance(self.binding, StageBinding):
            raise ValueError("binding must be StageBinding")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "result", result)
        object.__setattr__(
            self,
            "evidence_sha256",
            _sha(self.evidence_sha256, "evidence_sha256", sha256_only=True),
        )
        if self.failure_fingerprint is not None:
            object.__setattr__(
                self,
                "failure_fingerprint",
                _required_text(self.failure_fingerprint, "failure_fingerprint"),
            )
        if result is EvidenceResult.PASS and self.failure_fingerprint is not None:
            raise ValueError("PASS evidence cannot contain a failure fingerprint")
        if result is EvidenceResult.FAIL and self.failure_fingerprint is None:
            raise ValueError("FAIL evidence requires a failure fingerprint")

    @property
    def fingerprint(self) -> str:
        return self.failure_fingerprint or self.evidence_sha256


@dataclass(frozen=True)
class StageRequest:
    """Data-only request: no path, command, credential, or mutation callback."""

    binding: StageBinding
    prior_evidence: Tuple[StageEvidence, ...] = ()
    repair: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.binding, StageBinding):
            raise ValueError("binding must be StageBinding")
        evidence = tuple(self.prior_evidence)
        if any(not isinstance(item, StageEvidence) for item in evidence):
            raise ValueError("prior_evidence must contain StageEvidence values")
        object.__setattr__(self, "prior_evidence", evidence)
        if not isinstance(self.repair, bool):
            raise ValueError("repair must be a boolean")


@runtime_checkable
class ExecuteLifecycleAdapter(Protocol):
    def execute(self, request: StageRequest) -> StageEvidence: ...


@runtime_checkable
class TestLifecycleAdapter(Protocol):
    def test(self, request: StageRequest) -> StageEvidence: ...


@runtime_checkable
class AuditLifecycleAdapter(Protocol):
    def audit(self, request: StageRequest) -> StageEvidence: ...


@runtime_checkable
class LifecycleStageAdapter(
    ExecuteLifecycleAdapter, TestLifecycleAdapter, AuditLifecycleAdapter, Protocol
):
    """Aggregate opt-in boundary around existing isolated stage runners."""


@dataclass(frozen=True)
class OrchestrationSnapshot:
    """Immutable, non-authoritative state for one task and candidate lifecycle."""

    binding: StageBinding
    state: OrchestrationState
    budget: FixLoopBudget
    evidence: Tuple[StageEvidence, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.binding, StageBinding):
            raise ValueError("binding must be StageBinding")
        try:
            state = (
                self.state
                if isinstance(self.state, OrchestrationState)
                else OrchestrationState(str(self.state).strip().lower())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown orchestration state") from exc
        if not isinstance(self.budget, FixLoopBudget):
            raise ValueError("budget must be FixLoopBudget")
        evidence = tuple(self.evidence)
        if any(not isinstance(item, StageEvidence) for item in evidence):
            raise ValueError("evidence must contain StageEvidence values")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "reason", str(self.reason))


_AUTHORITY_RANK = {
    Authority.DENIED: 0,
    Authority.OWNER_ONLY: 1,
    Authority.AUTONOMOUS: 2,
}


def _coerce_authority(value: object) -> Optional[Authority]:
    if isinstance(value, Authority):
        return value
    if isinstance(value, str):
        try:
            return Authority(value.strip().lower())
        except ValueError:
            return None
    return None


def _coerce_action(value: object) -> Optional[LifecycleAction]:
    if isinstance(value, LifecycleAction):
        return value
    if isinstance(value, str):
        try:
            return LifecycleAction(value.strip().lower())
        except ValueError:
            return None
    return None


def _coerce_state(value: object) -> Optional[LifecycleState]:
    if isinstance(value, LifecycleState):
        return value
    if isinstance(value, str):
        try:
            return LifecycleState(value.strip().lower())
        except ValueError:
            return None
    return None


def _coerce_outcome(value: object) -> Optional[TerminalOutcome]:
    if isinstance(value, TerminalOutcome):
        return value
    if isinstance(value, str):
        try:
            return TerminalOutcome(value.strip().lower())
        except ValueError:
            return None
    return None


def intersect_authorities(*values: object) -> Authority:
    """Return the least authority; empty, missing, or unknown input is denied."""

    if not values:
        return Authority.DENIED
    normalized = [_coerce_authority(value) for value in values]
    if any(value is None for value in normalized):
        return Authority.DENIED
    return min(normalized, key=lambda item: _AUTHORITY_RANK[item])  # type: ignore[index, return-value]


def authority_for(
    action: Union[LifecycleAction, str],
    *layers: Union[Mapping[object, object], Sequence[AuthorityBinding]],
) -> Authority:
    """Intersect action authority across layers, with absent entries denied."""

    normalized_action = _coerce_action(action)
    if normalized_action is None or not layers:
        return Authority.DENIED
    values = []
    for layer in layers:
        if isinstance(layer, Mapping):
            sentinel = object()
            value = layer.get(normalized_action, sentinel)
            if value is sentinel:
                value = layer.get(normalized_action.value, sentinel)
            if value is sentinel:
                return Authority.DENIED
            values.append(value)
            continue
        if any(not isinstance(item, AuthorityBinding) for item in layer):
            return Authority.DENIED
        found = [item.authority for item in layer if item.action is normalized_action]
        if len(found) != 1:
            return Authority.DENIED
        values.append(found[0])
    return intersect_authorities(*values)


# Required action and evidence kinds for every legal edge.  An absent edge is
# illegal.  This table is data, which keeps transition evaluation deterministic.
_LEGAL_TRANSITIONS = {
    (LifecycleState.PENDING, LifecycleState.REVIEW):
        (LifecycleAction.VALIDATE, frozenset({"task_validated"})),
    (LifecycleState.REVIEW, LifecycleState.PENDING_CODEX):
        (LifecycleAction.IMPLEMENT, frozenset({"implementation", "required_checks"})),
    (LifecycleState.PENDING_CODEX, LifecycleState.APPROVED):
        (LifecycleAction.AUDIT, frozenset({"stage_01c_pass"})),
    (LifecycleState.PENDING_CODEX, LifecycleState.REVIEW):
        (LifecycleAction.FIX, frozenset({"stage_01c_failure"})),
    (LifecycleState.APPROVED, LifecycleState.TERMINAL):
        (LifecycleAction.TERMINATE, frozenset({"terminal_outcome"})),
}


# Minimum evidence that makes an explicit snapshot state supportable.  These
# requirements are deliberately weaker than path reconstruction: REVIEW may be
# either the first review or a bounded fix-loop return, while BLOCKED/FAILED may
# be reached from any live state.  Transition evaluation and reconciliation
# check these bindings before trusting a snapshot's claimed state.
_STATE_EVIDENCE_REQUIREMENTS = {
    LifecycleState.PENDING: frozenset(),
    LifecycleState.REVIEW: frozenset({"task_validated"}),
    LifecycleState.PENDING_CODEX: frozenset(
        {"task_validated", "implementation", "required_checks"}
    ),
    LifecycleState.APPROVED: frozenset(
        {"task_validated", "implementation", "required_checks", "stage_01c_pass"}
    ),
    LifecycleState.BLOCKED: frozenset({"block_reason"}),
    LifecycleState.FAILED: frozenset({"failure"}),
    LifecycleState.TERMINAL: frozenset(
        {
            "task_validated",
            "implementation",
            "required_checks",
            "stage_01c_pass",
            "terminal_outcome",
        }
    ),
}


def _missing_state_evidence(snapshot: LifecycleSnapshot) -> Tuple[str, ...]:
    present = {item.kind for item in snapshot.evidence}
    return tuple(sorted(_STATE_EVIDENCE_REQUIREMENTS[snapshot.state] - present))


def legal_transitions(state: Union[LifecycleState, str]) -> Tuple[LifecycleState, ...]:
    normalized = _coerce_state(state)
    if normalized is None:
        return ()
    targets = [target for (source, target) in _LEGAL_TRANSITIONS if source is normalized]
    if normalized not in (LifecycleState.BLOCKED, LifecycleState.FAILED, LifecycleState.TERMINAL):
        targets.extend((LifecycleState.BLOCKED, LifecycleState.FAILED))
    return tuple(sorted(set(targets), key=lambda item: item.value))


def _transition_requirement(
    source: LifecycleState, target: LifecycleState
) -> Optional[Tuple[LifecycleAction, frozenset[str]]]:
    requirement = _LEGAL_TRANSITIONS.get((source, target))
    if requirement is not None:
        return requirement
    if source not in (LifecycleState.BLOCKED, LifecycleState.FAILED, LifecycleState.TERMINAL):
        if target is LifecycleState.BLOCKED:
            return LifecycleAction.TERMINATE, frozenset({"block_reason"})
        if target is LifecycleState.FAILED:
            return LifecycleAction.TERMINATE, frozenset({"failure"})
    return None


def evaluate_transition(
    snapshot: LifecycleSnapshot,
    target_state: Union[LifecycleState, str],
    *,
    evidence: Iterable[EvidenceBinding] = (),
    terminal_outcome: Optional[Union[TerminalOutcome, str]] = None,
    authority_layers: Sequence[
        Union[Mapping[object, object], Sequence[AuthorityBinding]]
    ] = (),
    expected_identity: Optional[TaskIdentity] = None,
) -> TransitionDecision:
    """Evaluate a proposed transition without changing any state."""

    target = _coerce_state(target_state)
    if target is None:
        return TransitionDecision(
            False, "unknown target state", None, Authority.DENIED, snapshot.state, None
        )
    if expected_identity is None or expected_identity != snapshot.identity:
        return TransitionDecision(
            False, "missing or mismatched immutable identity", None,
            Authority.DENIED, snapshot.state, target,
        )
    missing_source_evidence = _missing_state_evidence(snapshot)
    if missing_source_evidence:
        return TransitionDecision(
            False,
            f"source state lacks required evidence: {', '.join(missing_source_evidence)}",
            None,
            Authority.DENIED,
            snapshot.state,
            target,
        )
    requirement = _transition_requirement(snapshot.state, target)
    if requirement is None:
        return TransitionDecision(
            False, "illegal transition", None, Authority.DENIED, snapshot.state, target
        )
    action, required_evidence = requirement

    supplied = tuple(evidence)
    if any(not isinstance(item, EvidenceBinding) for item in supplied):
        return TransitionDecision(
            False, "invalid evidence binding", action, Authority.DENIED, snapshot.state, target
        )
    if any(item.task_id != snapshot.identity.task_id for item in supplied):
        return TransitionDecision(
            False, "evidence identity mismatch", action, Authority.DENIED, snapshot.state, target
        )
    present_kinds = {item.kind for item in snapshot.evidence + supplied}
    missing = sorted(required_evidence - present_kinds)
    if missing:
        return TransitionDecision(
            False, f"missing evidence: {', '.join(missing)}", action,
            Authority.DENIED, snapshot.state, target,
        )

    outcome = _coerce_outcome(terminal_outcome)
    if target is LifecycleState.TERMINAL:
        if outcome is None:
            return TransitionDecision(
                False, "terminal transition requires a known outcome", action,
                Authority.DENIED, snapshot.state, target,
            )
    elif terminal_outcome is not None:
        return TransitionDecision(
            False, "outcome supplied for non-terminal transition", action,
            Authority.DENIED, snapshot.state, target,
        )

    layers = (snapshot.authority,) + tuple(authority_layers)
    effective = authority_for(action, *layers)
    if effective is not Authority.AUTONOMOUS:
        reason = "owner action required" if effective is Authority.OWNER_ONLY else "authority denied"
        return TransitionDecision(
            False, reason, action, effective, snapshot.state, target
        )
    return TransitionDecision(True, "allowed in shadow model", action, effective, snapshot.state, target)


def apply_transition(
    snapshot: LifecycleSnapshot,
    target_state: Union[LifecycleState, str],
    *,
    evidence: Iterable[EvidenceBinding] = (),
    terminal_outcome: Optional[Union[TerminalOutcome, str]] = None,
    authority_layers: Sequence[
        Union[Mapping[object, object], Sequence[AuthorityBinding]]
    ] = (),
    expected_identity: Optional[TaskIdentity] = None,
) -> LifecycleSnapshot:
    """Return a new snapshot or raise LifecycleDenied; perform no side effect."""

    supplied = tuple(evidence)
    decision = evaluate_transition(
        snapshot,
        target_state,
        evidence=supplied,
        terminal_outcome=terminal_outcome,
        authority_layers=authority_layers,
        expected_identity=expected_identity,
    )
    if not decision.allowed or decision.to_state is None:
        raise LifecycleDenied(decision.reason)
    outcome = _coerce_outcome(terminal_outcome)
    return replace(
        snapshot,
        state=decision.to_state,
        terminal_outcome=outcome,
        evidence=tuple(sorted(set(snapshot.evidence + supplied))),
        version=snapshot.version + 1,
    )


def build_idempotency_key(
    identity: TaskIdentity,
    from_state: Union[LifecycleState, str],
    to_state: Union[LifecycleState, str],
    *,
    evidence: Iterable[EvidenceBinding] = (),
    attempt: int = 0,
) -> str:
    """Build a stable key solely from immutable, canonical inputs."""

    source = _coerce_state(from_state)
    target = _coerce_state(to_state)
    if source is None or target is None:
        raise ValueError("idempotency key requires known states")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
        raise ValueError("attempt must be a non-negative integer")
    bindings = tuple(evidence)
    if any(not isinstance(item, EvidenceBinding) for item in bindings):
        raise ValueError("invalid evidence binding")
    if any(item.task_id != identity.task_id for item in bindings):
        raise ValueError("evidence identity mismatch")
    payload = {
        "attempt": attempt,
        "evidence": [
            {"kind": item.kind, "sha256": item.sha256, "task_id": item.task_id}
            for item in sorted(set(bindings))
        ],
        "from": source.value,
        "identity": {
            "project_id": identity.project_id,
            "source_sha": identity.source_sha,
            "task_id": identity.task_id,
            "task_sha256": identity.task_sha256,
        },
        "to": target.value,
        "version": 1,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "utl:v1:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()


def classify_retry(failure: Union[FailureKind, str]) -> RetryClass:
    try:
        kind = failure if isinstance(failure, FailureKind) else FailureKind(str(failure).strip().lower())
    except (TypeError, ValueError):
        return RetryClass.NEVER
    if kind in (FailureKind.TRANSIENT_INFRASTRUCTURE, FailureKind.TIMEOUT):
        return RetryClass.TRANSIENT
    if kind in (FailureKind.AUDIT_REJECTED, FailureKind.CHECK_FAILED):
        return RetryClass.FIX_LOOP
    if kind in (FailureKind.AUTHORITY_DENIED, FailureKind.OWNER_GATE):
        return RetryClass.OWNER_ACTION_REQUIRED
    return RetryClass.NEVER


def decide_retry(
    failure: Union[FailureKind, str],
    budget: FixLoopBudget,
    *,
    failure_fingerprint: str,
) -> RetryDecision:
    """Consume a failure deterministically and enforce both finite stop limits."""

    fingerprint = _required_text(failure_fingerprint, "failure_fingerprint")
    classification = classify_retry(failure)
    repeated = (
        budget.repeated_failures + 1
        if fingerprint == budget.last_failure_fingerprint
        else 1
    )
    next_fix_attempts = budget.fix_attempts + (classification is RetryClass.FIX_LOOP)
    # Do not create an invalid budget after exhaustion; the stopped snapshot
    # remains pinned at the configured finite maximum.
    stored_fix_attempts = min(next_fix_attempts, budget.max_fix_attempts)
    next_budget = replace(
        budget,
        fix_attempts=stored_fix_attempts,
        repeated_failures=repeated,
        last_failure_fingerprint=fingerprint,
    )
    if classification is RetryClass.NEVER:
        return RetryDecision(classification, False, "failure is not retryable", next_budget)
    if classification is RetryClass.OWNER_ACTION_REQUIRED:
        return RetryDecision(classification, False, "owner action required", next_budget)
    if repeated >= budget.max_repeated_failures:
        return RetryDecision(classification, False, "repeated-failure limit reached", next_budget)
    if classification is RetryClass.FIX_LOOP and next_fix_attempts > budget.max_fix_attempts:
        return RetryDecision(classification, False, "fix-loop budget exhausted", next_budget)
    return RetryDecision(classification, True, "retry allowed within finite budget", next_budget)


def start_orchestration(
    binding: StageBinding, budget: FixLoopBudget
) -> OrchestrationSnapshot:
    """Create an EXECUTE shadow without invoking an adapter or changing runtime."""

    if not isinstance(binding, StageBinding):
        raise ValueError("binding must be StageBinding")
    if not isinstance(budget, FixLoopBudget):
        raise ValueError("budget must be FixLoopBudget")
    return OrchestrationSnapshot(binding, OrchestrationState.EXECUTE, budget)


def _same_lifecycle(left: StageBinding, right: StageBinding) -> bool:
    """Compare the fields a repair is never permitted to replace."""

    return (
        left.task == right.task
        and left.validated_scope_sha256 == right.validated_scope_sha256
        and left.candidate.base_sha == right.candidate.base_sha
        and left.policy_version == right.policy_version
        and left.evidence_version == right.evidence_version
    )


def _matching_pass(
    evidence: Sequence[StageEvidence],
    stage: AdapterStage,
    binding: StageBinding,
) -> bool:
    return any(
        item.stage is stage
        and item.result is EvidenceResult.PASS
        and item.binding == binding
        for item in evidence
    )


def validate_stage_evidence(
    snapshot: OrchestrationSnapshot,
    evidence: StageEvidence,
) -> None:
    """Reject missing, stale, cross-task, mismatched, and out-of-order evidence."""

    if not isinstance(snapshot, OrchestrationSnapshot):
        raise LifecycleEvidenceDenied("missing orchestration snapshot")
    if not isinstance(evidence, StageEvidence):
        raise LifecycleEvidenceDenied("missing or invalid stage evidence")
    if snapshot.state in (OrchestrationState.APPROVED, OrchestrationState.FAILED):
        raise LifecycleEvidenceDenied("orchestration is already terminal")

    expected_stage = {
        OrchestrationState.EXECUTE: AdapterStage.EXECUTE,
        OrchestrationState.TEST: AdapterStage.TEST,
        OrchestrationState.AUDIT: AdapterStage.AUDIT,
        OrchestrationState.FIX_LOOP: AdapterStage.EXECUTE,
    }.get(snapshot.state)
    if evidence.stage is not expected_stage:
        raise LifecycleEvidenceDenied(
            f"out-of-order evidence: expected {expected_stage.value if expected_stage else 'none'}"
        )

    if snapshot.state is OrchestrationState.FIX_LOOP:
        if not _same_lifecycle(evidence.binding, snapshot.binding):
            raise LifecycleEvidenceDenied(
                "repair evidence changed task, scope, base, policy, or evidence version"
            )
        if evidence.binding.attempt != snapshot.binding.attempt + 1:
            raise LifecycleEvidenceDenied("repair evidence has stale or wrong attempt")
    elif evidence.binding != snapshot.binding:
        raise LifecycleEvidenceDenied(
            "evidence task, scope, candidate, attempt, policy, or version mismatch"
        )

    if any(
        item.evidence_sha256 == evidence.evidence_sha256
        for item in snapshot.evidence
    ):
        raise LifecycleEvidenceDenied("stage evidence is stale or replayed")

    if evidence.stage is AdapterStage.TEST and not _matching_pass(
        snapshot.evidence, AdapterStage.EXECUTE, evidence.binding
    ):
        raise LifecycleEvidenceDenied("TEST evidence lacks matching EXECUTE PASS")
    if evidence.stage is AdapterStage.AUDIT and not _matching_pass(
        snapshot.evidence, AdapterStage.TEST, evidence.binding
    ):
        raise LifecycleEvidenceDenied("AUDIT evidence lacks matching TEST PASS")


def apply_stage_evidence(
    snapshot: OrchestrationSnapshot,
    evidence: StageEvidence,
) -> OrchestrationSnapshot:
    """Apply one validated Slice 3 result and return a new shadow snapshot."""

    validate_stage_evidence(snapshot, evidence)
    collected = snapshot.evidence + (evidence,)

    if evidence.stage is AdapterStage.EXECUTE:
        if evidence.result is EvidenceResult.FAIL:
            return replace(
                snapshot,
                binding=evidence.binding,
                state=OrchestrationState.FAILED,
                evidence=collected,
                reason="EXECUTE adapter reported failure",
            )
        return replace(
            snapshot,
            binding=evidence.binding,
            state=OrchestrationState.TEST,
            evidence=collected,
            reason="matching EXECUTE PASS accepted",
        )

    if evidence.stage is AdapterStage.TEST:
        if evidence.result is EvidenceResult.PASS:
            return replace(
                snapshot,
                state=OrchestrationState.AUDIT,
                evidence=collected,
                reason="matching TEST PASS accepted",
            )
        retry = decide_retry(
            FailureKind.CHECK_FAILED,
            snapshot.budget,
            failure_fingerprint=evidence.fingerprint,
        )
        return replace(
            snapshot,
            state=(
                OrchestrationState.FIX_LOOP
                if retry.should_retry
                else OrchestrationState.FAILED
            ),
            budget=retry.budget,
            evidence=collected,
            reason=retry.reason,
        )

    # AUDIT can reach APPROVED only after validate_stage_evidence found an
    # exact TEST PASS for this task/candidate/attempt/policy context.
    if evidence.result is EvidenceResult.PASS:
        return replace(
            snapshot,
            state=OrchestrationState.APPROVED,
            evidence=collected,
            reason="matching TEST PASS and Stage 01C PASS accepted",
        )
    retry = decide_retry(
        FailureKind.AUDIT_REJECTED,
        snapshot.budget,
        failure_fingerprint=evidence.fingerprint,
    )
    return replace(
        snapshot,
        state=(
            OrchestrationState.FIX_LOOP
            if retry.should_retry
            else OrchestrationState.FAILED
        ),
        budget=retry.budget,
        evidence=collected,
        reason=retry.reason,
    )


def fail_orchestration(
    snapshot: OrchestrationSnapshot, reason: str
) -> OrchestrationSnapshot:
    """Fail closed on an adapter exception or invalid adapter return."""

    if not isinstance(snapshot, OrchestrationSnapshot):
        raise ValueError("snapshot must be OrchestrationSnapshot")
    return replace(
        snapshot,
        state=OrchestrationState.FAILED,
        reason=_required_text(reason, "reason"),
    )


def reconcile(
    shadow: LifecycleSnapshot,
    observed: LifecycleSnapshot,
    *,
    action: Optional[Union[LifecycleAction, str]] = None,
    authority_layers: Sequence[
        Union[Mapping[object, object], Sequence[AuthorityBinding]]
    ] = (),
) -> ReconciliationDecision:
    """Classify drift without mutating either snapshot or an external system."""

    if shadow.identity != observed.identity:
        return ReconciliationDecision(
            ReconciliationDecisionType.BLOCK, "immutable identity drift"
        )
    for label, snapshot in (("shadow", shadow), ("observed", observed)):
        missing = _missing_state_evidence(snapshot)
        if missing:
            return ReconciliationDecision(
                ReconciliationDecisionType.BLOCK,
                f"{label} state lacks required evidence: {', '.join(missing)}",
            )
    normalized_action = _coerce_action(action) if action is not None else None
    if action is not None and normalized_action is None:
        return ReconciliationDecision(
            ReconciliationDecisionType.BLOCK, "unknown reconciliation action"
        )
    if shadow.state is observed.state and shadow.terminal_outcome is observed.terminal_outcome:
        if normalized_action is not None:
            effective = authority_for(
                normalized_action, shadow.authority, *tuple(authority_layers)
            )
            if effective is Authority.DENIED:
                return ReconciliationDecision(
                    ReconciliationDecisionType.BLOCK, "reconciliation authority denied"
                )
            if effective is Authority.OWNER_ONLY:
                return ReconciliationDecision(
                    ReconciliationDecisionType.OWNER_ACTION_REQUIRED,
                    "reconciliation is owner-gated",
                )
        return ReconciliationDecision(
            ReconciliationDecisionType.NO_ACTION, "shadow and observation agree", shadow.state
        )
    if observed.state in legal_transitions(shadow.state):
        requirement = _transition_requirement(shadow.state, observed.state)
        required_action = requirement[0] if requirement is not None else None
        if normalized_action is None or normalized_action is not required_action:
            return ReconciliationDecision(
                ReconciliationDecisionType.BLOCK,
                "missing or mismatched reconciliation action",
            )
        effective = authority_for(
            normalized_action, shadow.authority, *tuple(authority_layers)
        )
        if effective is Authority.DENIED:
            return ReconciliationDecision(
                ReconciliationDecisionType.BLOCK, "reconciliation authority denied"
            )
        if effective is Authority.OWNER_ONLY:
            return ReconciliationDecision(
                ReconciliationDecisionType.OWNER_ACTION_REQUIRED,
                "reconciliation is owner-gated",
            )
        required_evidence = requirement[1] if requirement is not None else frozenset()
        if not required_evidence.issubset({item.kind for item in observed.evidence}):
            return ReconciliationDecision(
                ReconciliationDecisionType.BLOCK,
                "observed advance lacks transition evidence",
            )
        return ReconciliationDecision(
            ReconciliationDecisionType.ADVANCE_SHADOW,
            "observed state is one legal edge ahead",
            observed.state,
        )
    if shadow.state in legal_transitions(observed.state):
        requirement = _transition_requirement(observed.state, shadow.state)
        required_action = requirement[0] if requirement is not None else None
        if normalized_action is None or normalized_action is not required_action:
            return ReconciliationDecision(
                ReconciliationDecisionType.BLOCK,
                "missing or mismatched reconciliation action",
            )
        effective = authority_for(
            normalized_action, shadow.authority, *tuple(authority_layers)
        )
        if effective is Authority.DENIED:
            return ReconciliationDecision(
                ReconciliationDecisionType.BLOCK, "reconciliation authority denied"
            )
        if effective is Authority.OWNER_ONLY:
            return ReconciliationDecision(
                ReconciliationDecisionType.OWNER_ACTION_REQUIRED,
                "reconciliation is owner-gated",
            )
        required_evidence = requirement[1] if requirement is not None else frozenset()
        if not required_evidence.issubset({item.kind for item in shadow.evidence}):
            return ReconciliationDecision(
                ReconciliationDecisionType.BLOCK,
                "shadow advance lacks transition evidence",
            )
        return ReconciliationDecision(
            ReconciliationDecisionType.KEEP_SHADOW,
            "supported shadow state is one legal edge ahead",
            shadow.state,
        )
    return ReconciliationDecision(
        ReconciliationDecisionType.BLOCK, "non-adjacent or illegal lifecycle drift"
    )


__all__ = [
    "AdapterStage",
    "AuditLifecycleAdapter",
    "Authority",
    "AuthorityBinding",
    "CandidateBinding",
    "EvidenceBinding",
    "EvidenceResult",
    "ExecuteLifecycleAdapter",
    "FailureKind",
    "FixLoopBudget",
    "LifecycleAction",
    "LifecycleDenied",
    "LifecycleEvidenceDenied",
    "LifecycleSnapshot",
    "LifecycleStageAdapter",
    "LifecycleState",
    "OrchestrationSnapshot",
    "OrchestrationState",
    "ReconciliationDecision",
    "ReconciliationDecisionType",
    "RetryClass",
    "RetryDecision",
    "StageBinding",
    "StageEvidence",
    "StageRequest",
    "TaskIdentity",
    "TerminalOutcome",
    "TestLifecycleAdapter",
    "TransitionDecision",
    "apply_transition",
    "apply_stage_evidence",
    "authority_for",
    "build_idempotency_key",
    "classify_retry",
    "decide_retry",
    "evaluate_transition",
    "fail_orchestration",
    "intersect_authorities",
    "legal_transitions",
    "reconcile",
    "start_orchestration",
    "validate_stage_evidence",
]
