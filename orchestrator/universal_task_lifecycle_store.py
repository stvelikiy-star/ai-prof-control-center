"""Transactional persistence contracts for the universal task lifecycle.

This module deliberately contains no production database or queue adapter.  It
defines the semantics a durable adapter must implement and provides a
deterministic in-memory implementation for tests and shadow evaluation.

Lifecycle authority remains in :mod:`universal_task_lifecycle`.  Persisting a
snapshot never grants authority and the store refuses to replace the identity
bound to an existing lifecycle record.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Generic, Mapping, Protocol, TypeVar, runtime_checkable

try:
    from orchestrator.universal_task_lifecycle import StageEvidence
except ImportError:  # pragma: no cover - direct service-script import form
    from universal_task_lifecycle import StageEvidence  # type: ignore[no-redef]


class LifecycleStoreError(RuntimeError):
    """Base class for fail-closed lifecycle persistence errors."""


class StaleLifecycleVersion(LifecycleStoreError):
    """The caller's expected lifecycle version is not current."""


class LifecycleIdentityMismatch(LifecycleStoreError):
    """A write attempted to change a task's immutable identity/SHA binding."""


class LeaseConflict(LifecycleStoreError):
    """A different owner holds an active lease."""


class LeaseNotOwned(LifecycleStoreError):
    """A lease mutation did not present the active owner and token."""


class DeduplicationConflict(LifecycleStoreError):
    """A deterministic key was reused for different immutable content."""


class TransactionClosed(LifecycleStoreError):
    """An operation was attempted outside an active transaction."""


@dataclass(frozen=True)
class LifecycleRecord:
    task_key: str
    version: int
    identity: object
    snapshot: object


@dataclass(frozen=True)
class Lease:
    task_key: str
    owner: str
    token: str
    acquired_at: int
    expires_at: int


@dataclass(frozen=True)
class InboxEvent:
    provider: str
    event_id: str
    action_key: str
    received_at: int


@dataclass(frozen=True)
class LedgerEntry:
    entry_key: str
    task_key: str
    lifecycle_version: int
    action: str
    payload_json: str
    recorded_at: int


@dataclass(frozen=True)
class StageEvidenceRecord:
    """Append-only Slice 3 evidence pinned to a lifecycle store version."""

    evidence_key: str
    task_key: str
    lifecycle_version: int
    evidence: object
    recorded_at: int


@dataclass(frozen=True)
class ProjectionIntent:
    intent_key: str
    task_key: str
    lifecycle_version: int
    projection: str
    payload_json: str
    created_at: int


@dataclass(frozen=True)
class ProjectionAcknowledgement:
    intent_key: str
    acknowledgement_key: str
    acknowledged_at: int


@runtime_checkable
class LifecycleTransaction(Protocol):
    """Atomic operations required from a durable lifecycle transaction."""

    def get_lifecycle(self, task_key: str) -> LifecycleRecord | None: ...

    def compare_and_swap(
        self,
        task_key: str,
        expected_version: int | None,
        identity: object,
        snapshot: object,
    ) -> LifecycleRecord: ...

    def acquire_lease(self, task_key: str, owner: str, ttl: int) -> Lease: ...

    def renew_lease(
        self, task_key: str, owner: str, token: str, ttl: int
    ) -> Lease: ...

    def release_lease(self, task_key: str, owner: str, token: str) -> bool: ...

    def claim_inbox_event(
        self, provider: str, event_id: str, action_key: str
    ) -> bool: ...

    def append_ledger_entry(
        self,
        entry_key: str,
        task_key: str,
        lifecycle_version: int,
        action: str,
        payload: Mapping[str, object] | None = None,
    ) -> LedgerEntry: ...

    def append_stage_evidence(
        self,
        evidence_key: str,
        task_key: str,
        lifecycle_version: int,
        evidence: object,
    ) -> StageEvidenceRecord: ...

    def enqueue_projection(
        self,
        intent_key: str,
        task_key: str,
        lifecycle_version: int,
        projection: str,
        payload: Mapping[str, object] | None = None,
    ) -> ProjectionIntent: ...

    def acknowledge_projection(
        self, intent_key: str, acknowledgement_key: str
    ) -> bool: ...


T = TypeVar("T")


@dataclass(frozen=True)
class InboxActionResult(Generic[T]):
    accepted: bool
    value: T | None = None


@runtime_checkable
class LifecycleStore(Protocol):
    """Explicit boundary implemented by a future durable store adapter."""

    def transaction(self) -> AbstractContextManager[LifecycleTransaction]: ...

    def transact_inbox_event(
        self,
        provider: str,
        event_id: str,
        action_key: str,
        action: Callable[[LifecycleTransaction], T],
    ) -> InboxActionResult[T]: ...


def deterministic_key(namespace: str, *parts: object) -> str:
    """Build a stable idempotency key without relying on process hash state."""
    if not namespace:
        raise ValueError("namespace must not be empty")
    encoded = json.dumps(
        [namespace, *parts],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return f"{namespace}:{hashlib.sha256(encoded).hexdigest()}"


def _canonical_payload(payload: Mapping[str, object] | None) -> str:
    try:
        return json.dumps(
            dict(payload or {}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LifecycleStoreError("payload must be canonical JSON data") from exc


def _require_text(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _transaction_operation(method):
    """Poison an active transaction when any public operation fails.

    Validation errors intentionally use ``ValueError`` rather than a store
    error, and copying/canonicalization can also reject caller-owned values.
    Keeping the rollback rule at this boundary ensures none of those failures
    can be caught inside a ``with`` block to commit preceding mutations.
    """

    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            if self._active:
                self._aborted = True
            raise

    return guarded


class InMemoryLifecycleStore:
    """Deterministic, serializable test fake for the durable store contract.

    A transaction owns the store lock for its complete lifetime and works on a
    private copy.  It publishes that copy only on successful exit.  Store
    operation errors poison the transaction, so catching a stale-write,
    validation, or lease error inside the ``with`` block cannot accidentally
    commit earlier writes.
    """

    def __init__(self, clock: Callable[[], int] | None = None) -> None:
        self._clock = clock or (lambda: 0)
        self._lock = threading.RLock()
        self._lifecycles: dict[str, LifecycleRecord] = {}
        self._leases: dict[str, Lease] = {}
        self._inbox: dict[tuple[str, str], InboxEvent] = {}
        self._ledger: dict[str, LedgerEntry] = {}
        self._stage_evidence: dict[str, StageEvidenceRecord] = {}
        self._outbox: dict[str, ProjectionIntent] = {}
        self._acks: dict[str, ProjectionAcknowledgement] = {}
        self._lease_sequence = 0

    def transaction(self) -> "InMemoryLifecycleTransaction":
        return InMemoryLifecycleTransaction(self)

    def transact_inbox_event(
        self,
        provider: str,
        event_id: str,
        action_key: str,
        action: Callable[[LifecycleTransaction], T],
    ) -> InboxActionResult[T]:
        """Claim an event and run its lifecycle action in one transaction.

        A repeated provider/event pair returns ``accepted=False`` without
        invoking ``action``.  If ``action`` fails, the inbox claim and every
        lifecycle/ledger/outbox mutation are rolled back together.
        """
        with self.transaction() as transaction:
            if not transaction.claim_inbox_event(provider, event_id, action_key):
                return InboxActionResult(accepted=False)
            return InboxActionResult(accepted=True, value=action(transaction))

    # Read-only inspection helpers are intentionally copies.  Mutation tests
    # cannot use them to change append-only or identity-bound state.
    def lifecycle(self, task_key: str) -> LifecycleRecord | None:
        with self._lock:
            return copy.deepcopy(self._lifecycles.get(task_key))

    def lease(self, task_key: str) -> Lease | None:
        with self._lock:
            return copy.deepcopy(self._leases.get(task_key))

    def inbox_events(self) -> tuple[InboxEvent, ...]:
        with self._lock:
            return tuple(copy.deepcopy(self._inbox[key]) for key in sorted(self._inbox))

    def ledger_entries(self) -> tuple[LedgerEntry, ...]:
        with self._lock:
            return tuple(copy.deepcopy(self._ledger[key]) for key in sorted(self._ledger))

    def stage_evidence(self) -> tuple[StageEvidenceRecord, ...]:
        with self._lock:
            return tuple(
                copy.deepcopy(self._stage_evidence[key])
                for key in sorted(self._stage_evidence)
            )

    def projection_intents(self) -> tuple[ProjectionIntent, ...]:
        with self._lock:
            return tuple(copy.deepcopy(self._outbox[key]) for key in sorted(self._outbox))

    def projection_acknowledgements(
        self,
    ) -> tuple[ProjectionAcknowledgement, ...]:
        with self._lock:
            return tuple(copy.deepcopy(self._acks[key]) for key in sorted(self._acks))


class InMemoryLifecycleTransaction(AbstractContextManager["InMemoryLifecycleTransaction"]):
    def __init__(self, store: InMemoryLifecycleStore) -> None:
        self._store = store
        self._active = False
        self._aborted = False

    def __enter__(self) -> "InMemoryLifecycleTransaction":
        if self._active:
            raise LifecycleStoreError("transaction is already active")
        self._store._lock.acquire()
        self._active = True
        self._lifecycles = copy.deepcopy(self._store._lifecycles)
        self._leases = copy.deepcopy(self._store._leases)
        self._inbox = copy.deepcopy(self._store._inbox)
        self._ledger = copy.deepcopy(self._store._ledger)
        self._stage_evidence = copy.deepcopy(self._store._stage_evidence)
        self._outbox = copy.deepcopy(self._store._outbox)
        self._acks = copy.deepcopy(self._store._acks)
        self._lease_sequence = self._store._lease_sequence
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            if exc_type is None and not self._aborted:
                self._store._lifecycles = self._lifecycles
                self._store._leases = self._leases
                self._store._inbox = self._inbox
                self._store._ledger = self._ledger
                self._store._stage_evidence = self._stage_evidence
                self._store._outbox = self._outbox
                self._store._acks = self._acks
                self._store._lease_sequence = self._lease_sequence
        finally:
            self._active = False
            self._store._lock.release()
        return False

    def _ensure_active(self) -> None:
        if not self._active:
            raise TransactionClosed("transaction is not active")

    def _fail(self, error: LifecycleStoreError):
        self._aborted = True
        raise error

    def _now(self) -> int:
        value = self._store._clock()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            self._fail(LifecycleStoreError("clock must return a non-negative integer"))
        return value

    @_transaction_operation
    def get_lifecycle(self, task_key: str) -> LifecycleRecord | None:
        self._ensure_active()
        _require_text(task_key, "task_key")
        return copy.deepcopy(self._lifecycles.get(task_key))

    @_transaction_operation
    def compare_and_swap(
        self,
        task_key: str,
        expected_version: int | None,
        identity: object,
        snapshot: object,
    ) -> LifecycleRecord:
        self._ensure_active()
        _require_text(task_key, "task_key")
        if expected_version is not None and (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise ValueError("expected_version must be None or a non-negative integer")

        snapshot_identity = getattr(snapshot, "identity", identity)
        if snapshot_identity != identity:
            self._fail(
                LifecycleIdentityMismatch("snapshot identity does not match write identity")
            )

        current = self._lifecycles.get(task_key)
        if current is None:
            if expected_version is not None:
                self._fail(
                    StaleLifecycleVersion(
                        f"{task_key} does not exist; expected {expected_version}"
                    )
                )
            next_version = 0
        else:
            if current.identity != identity:
                self._fail(
                    LifecycleIdentityMismatch(
                        f"identity binding for {task_key} is immutable"
                    )
                )
            if expected_version != current.version:
                self._fail(
                    StaleLifecycleVersion(
                        f"{task_key} is at version {current.version}, not {expected_version}"
                    )
                )
            next_version = current.version + 1

        record = LifecycleRecord(
            task_key=task_key,
            version=next_version,
            identity=copy.deepcopy(identity),
            snapshot=copy.deepcopy(snapshot),
        )
        self._lifecycles[task_key] = record
        return copy.deepcopy(record)

    @_transaction_operation
    def acquire_lease(self, task_key: str, owner: str, ttl: int) -> Lease:
        self._ensure_active()
        _require_text(task_key, "task_key")
        _require_text(owner, "owner")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise ValueError("ttl must be a positive integer")
        now = self._now()
        current = self._leases.get(task_key)
        if current is not None and current.expires_at > now:
            if current.owner == owner:
                return copy.deepcopy(current)
            self._fail(LeaseConflict(f"{task_key} has an active lease"))

        self._lease_sequence += 1
        token = deterministic_key("lease", task_key, owner, self._lease_sequence)
        lease = Lease(task_key, owner, token, now, now + ttl)
        self._leases[task_key] = lease
        return copy.deepcopy(lease)

    @_transaction_operation
    def renew_lease(
        self, task_key: str, owner: str, token: str, ttl: int
    ) -> Lease:
        self._ensure_active()
        _require_text(task_key, "task_key")
        _require_text(owner, "owner")
        _require_text(token, "token")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            raise ValueError("ttl must be a positive integer")
        now = self._now()
        current = self._leases.get(task_key)
        if (
            current is None
            or current.owner != owner
            or current.token != token
            or current.expires_at <= now
        ):
            self._fail(LeaseNotOwned(f"no matching active lease for {task_key}"))
        renewed = Lease(
            task_key=current.task_key,
            owner=current.owner,
            token=current.token,
            acquired_at=current.acquired_at,
            expires_at=now + ttl,
        )
        self._leases[task_key] = renewed
        return copy.deepcopy(renewed)

    @_transaction_operation
    def release_lease(self, task_key: str, owner: str, token: str) -> bool:
        self._ensure_active()
        _require_text(task_key, "task_key")
        _require_text(owner, "owner")
        _require_text(token, "token")
        current = self._leases.get(task_key)
        if current is None:
            return False
        if current.owner != owner or current.token != token:
            self._fail(LeaseNotOwned(f"lease for {task_key} belongs to another owner"))
        del self._leases[task_key]
        return True

    @_transaction_operation
    def claim_inbox_event(
        self, provider: str, event_id: str, action_key: str
    ) -> bool:
        self._ensure_active()
        _require_text(provider, "provider")
        _require_text(event_id, "event_id")
        _require_text(action_key, "action_key")
        key = (provider, event_id)
        current = self._inbox.get(key)
        if current is not None:
            if current.action_key != action_key:
                self._fail(
                    DeduplicationConflict(
                        "provider/event identity was reused for another action"
                    )
                )
            return False
        self._inbox[key] = InboxEvent(provider, event_id, action_key, self._now())
        return True

    def _require_current_version(self, task_key: str, version: int) -> None:
        current = self._lifecycles.get(task_key)
        if current is None or current.version != version:
            self._fail(
                StaleLifecycleVersion(
                    f"{task_key} is not at lifecycle version {version}"
                )
            )

    @_transaction_operation
    def append_ledger_entry(
        self,
        entry_key: str,
        task_key: str,
        lifecycle_version: int,
        action: str,
        payload: Mapping[str, object] | None = None,
    ) -> LedgerEntry:
        self._ensure_active()
        _require_text(entry_key, "entry_key")
        _require_text(task_key, "task_key")
        _require_text(action, "action")
        payload_json = _canonical_payload(payload)
        current = self._ledger.get(entry_key)
        if current is not None:
            candidate = (
                current.task_key,
                current.lifecycle_version,
                current.action,
                current.payload_json,
            )
            if candidate != (task_key, lifecycle_version, action, payload_json):
                self._fail(DeduplicationConflict("ledger key content is immutable"))
            return copy.deepcopy(current)
        self._require_current_version(task_key, lifecycle_version)
        entry = LedgerEntry(
            entry_key,
            task_key,
            lifecycle_version,
            action,
            payload_json,
            self._now(),
        )
        self._ledger[entry_key] = entry
        return copy.deepcopy(entry)

    @_transaction_operation
    def append_stage_evidence(
        self,
        evidence_key: str,
        task_key: str,
        lifecycle_version: int,
        evidence: object,
    ) -> StageEvidenceRecord:
        """Append exact stage evidence, rejecting cross-task or stale records."""

        self._ensure_active()
        _require_text(evidence_key, "evidence_key")
        _require_text(task_key, "task_key")
        if (
            isinstance(lifecycle_version, bool)
            or not isinstance(lifecycle_version, int)
            or lifecycle_version < 0
        ):
            raise ValueError("lifecycle_version must be a non-negative integer")
        if not isinstance(evidence, StageEvidence):
            raise ValueError("evidence must be StageEvidence")
        current = self._stage_evidence.get(evidence_key)
        if current is not None:
            if (
                current.task_key,
                current.lifecycle_version,
                current.evidence,
            ) != (task_key, lifecycle_version, evidence):
                self._fail(
                    DeduplicationConflict("stage evidence key content is immutable")
                )
            return copy.deepcopy(current)

        self._require_current_version(task_key, lifecycle_version)
        lifecycle = self._lifecycles[task_key]
        binding = getattr(evidence, "binding", None)
        evidence_identity = getattr(binding, "task", None)
        if binding is None or evidence_identity is None:
            self._fail(LifecycleIdentityMismatch("stage evidence lacks task binding"))
        if evidence_identity != lifecycle.identity:
            self._fail(
                LifecycleIdentityMismatch(
                    "stage evidence identity does not match lifecycle identity"
                )
            )
        if getattr(evidence_identity, "task_id", None) != task_key:
            self._fail(
                LifecycleIdentityMismatch(
                    "stage evidence task_id does not match lifecycle key"
                )
            )
        record = StageEvidenceRecord(
            evidence_key,
            task_key,
            lifecycle_version,
            copy.deepcopy(evidence),
            self._now(),
        )
        self._stage_evidence[evidence_key] = record
        return copy.deepcopy(record)

    @_transaction_operation
    def enqueue_projection(
        self,
        intent_key: str,
        task_key: str,
        lifecycle_version: int,
        projection: str,
        payload: Mapping[str, object] | None = None,
    ) -> ProjectionIntent:
        self._ensure_active()
        _require_text(intent_key, "intent_key")
        _require_text(task_key, "task_key")
        _require_text(projection, "projection")
        payload_json = _canonical_payload(payload)
        current = self._outbox.get(intent_key)
        if current is not None:
            candidate = (
                current.task_key,
                current.lifecycle_version,
                current.projection,
                current.payload_json,
            )
            if candidate != (task_key, lifecycle_version, projection, payload_json):
                self._fail(DeduplicationConflict("outbox key content is immutable"))
            return copy.deepcopy(current)
        self._require_current_version(task_key, lifecycle_version)
        intent = ProjectionIntent(
            intent_key,
            task_key,
            lifecycle_version,
            projection,
            payload_json,
            self._now(),
        )
        self._outbox[intent_key] = intent
        return copy.deepcopy(intent)

    @_transaction_operation
    def acknowledge_projection(
        self, intent_key: str, acknowledgement_key: str
    ) -> bool:
        self._ensure_active()
        _require_text(intent_key, "intent_key")
        _require_text(acknowledgement_key, "acknowledgement_key")
        if intent_key not in self._outbox:
            self._fail(DeduplicationConflict("cannot acknowledge an unknown intent"))
        current = self._acks.get(intent_key)
        if current is not None:
            if current.acknowledgement_key != acknowledgement_key:
                self._fail(
                    DeduplicationConflict(
                        "projection intent already has a different acknowledgement"
                    )
                )
            return False
        self._acks[intent_key] = ProjectionAcknowledgement(
            intent_key, acknowledgement_key, self._now()
        )
        return True
