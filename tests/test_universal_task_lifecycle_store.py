from __future__ import annotations

from dataclasses import dataclass
import unittest

from orchestrator.universal_task_lifecycle_store import (
    DeduplicationConflict,
    InMemoryLifecycleStore,
    LeaseConflict,
    LeaseNotOwned,
    LifecycleIdentityMismatch,
    StaleLifecycleVersion,
    deterministic_key,
)


@dataclass(frozen=True)
class FakeIdentity:
    task_id: str
    project_id: str
    task_sha256: str
    base_sha: str


@dataclass(frozen=True)
class FakeSnapshot:
    identity: FakeIdentity
    state: str


class ManualClock:
    def __init__(self, value: int = 100) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


IDENTITY = FakeIdentity("TASK-1", "control-center", "a" * 64, "b" * 40)


class UniversalTaskLifecycleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = ManualClock()
        self.store = InMemoryLifecycleStore(clock=self.clock)

    def _create(self, state: str = "pending"):
        with self.store.transaction() as transaction:
            return transaction.compare_and_swap(
                "TASK-1", None, IDENTITY, FakeSnapshot(IDENTITY, state)
            )

    def test_compare_and_swap_versions_and_rejects_stale_write(self):
        created = self._create()
        self.assertEqual(created.version, 0)
        with self.store.transaction() as transaction:
            updated = transaction.compare_and_swap(
                "TASK-1", 0, IDENTITY, FakeSnapshot(IDENTITY, "review")
            )
        self.assertEqual(updated.version, 1)

        with self.assertRaises(StaleLifecycleVersion):
            with self.store.transaction() as transaction:
                transaction.compare_and_swap(
                    "TASK-1", 0, IDENTITY, FakeSnapshot(IDENTITY, "approved")
                )
        self.assertEqual(self.store.lifecycle("TASK-1").snapshot.state, "review")

    def test_cas_preserves_identity_and_snapshot_sha_binding(self):
        self._create()
        changed = FakeIdentity("TASK-1", "control-center", "c" * 64, "b" * 40)
        with self.assertRaises(LifecycleIdentityMismatch):
            with self.store.transaction() as transaction:
                transaction.compare_and_swap(
                    "TASK-1", 0, changed, FakeSnapshot(changed, "review")
                )
        with self.assertRaises(LifecycleIdentityMismatch):
            with self.store.transaction() as transaction:
                transaction.compare_and_swap(
                    "TASK-1", 0, IDENTITY, FakeSnapshot(changed, "review")
                )
        self.assertEqual(self.store.lifecycle("TASK-1").identity, IDENTITY)

    def test_active_lease_conflict_renew_release_and_expiry(self):
        with self.store.transaction() as transaction:
            lease = transaction.acquire_lease("TASK-1", "worker-a", 10)
        with self.store.transaction() as transaction:
            self.assertEqual(
                transaction.acquire_lease("TASK-1", "worker-a", 99), lease
            )
        with self.assertRaises(LeaseConflict):
            with self.store.transaction() as transaction:
                transaction.acquire_lease("TASK-1", "worker-b", 10)

        self.clock.value = 105
        with self.store.transaction() as transaction:
            renewed = transaction.renew_lease(
                "TASK-1", "worker-a", lease.token, 20
            )
        self.assertEqual(renewed.expires_at, 125)
        with self.assertRaises(LeaseNotOwned):
            with self.store.transaction() as transaction:
                transaction.release_lease("TASK-1", "worker-b", renewed.token)
        with self.store.transaction() as transaction:
            self.assertTrue(
                transaction.release_lease("TASK-1", "worker-a", renewed.token)
            )
        self.assertIsNone(self.store.lease("TASK-1"))

        with self.store.transaction() as transaction:
            first = transaction.acquire_lease("TASK-1", "worker-a", 5)
        self.clock.value = 111
        with self.store.transaction() as transaction:
            second = transaction.acquire_lease("TASK-1", "worker-b", 5)
        self.assertNotEqual(first.token, second.token)
        self.assertEqual(second.owner, "worker-b")

    def test_expired_lease_cannot_be_renewed(self):
        with self.store.transaction() as transaction:
            lease = transaction.acquire_lease("TASK-1", "worker-a", 2)
        self.clock.value = 102
        with self.assertRaises(LeaseNotOwned):
            with self.store.transaction() as transaction:
                transaction.renew_lease("TASK-1", "worker-a", lease.token, 5)

    def test_inbox_deduplicates_without_repeating_lifecycle_action(self):
        calls = []

        def action(transaction):
            calls.append("called")
            return transaction.compare_and_swap(
                "TASK-1", None, IDENTITY, FakeSnapshot(IDENTITY, "pending")
            ).version

        first = self.store.transact_inbox_event(
            "github", "delivery-1", "validate", action
        )
        second = self.store.transact_inbox_event(
            "github", "delivery-1", "validate", action
        )
        self.assertTrue(first.accepted)
        self.assertEqual(first.value, 0)
        self.assertFalse(second.accepted)
        self.assertIsNone(second.value)
        self.assertEqual(calls, ["called"])
        self.assertEqual(len(self.store.inbox_events()), 1)
        self.assertEqual(self.store.lifecycle("TASK-1").version, 0)

    def test_inbox_identity_cannot_be_reused_for_another_action(self):
        with self.store.transaction() as transaction:
            self.assertTrue(
                transaction.claim_inbox_event("github", "delivery-1", "validate")
            )
        with self.assertRaises(DeduplicationConflict):
            with self.store.transaction() as transaction:
                transaction.claim_inbox_event("github", "delivery-1", "merge")
        self.assertEqual(self.store.inbox_events()[0].action_key, "validate")

    def test_ledger_and_outbox_are_append_only_and_idempotent(self):
        self._create()
        entry_key = deterministic_key("ledger", "TASK-1", 0, "validated")
        intent_key = deterministic_key("outbox", "TASK-1", 0, "status")
        with self.store.transaction() as transaction:
            first_entry = transaction.append_ledger_entry(
                entry_key, "TASK-1", 0, "validated", {"ok": True}
            )
            first_intent = transaction.enqueue_projection(
                intent_key, "TASK-1", 0, "status", {"state": "pending"}
            )
        with self.store.transaction() as transaction:
            repeated_entry = transaction.append_ledger_entry(
                entry_key, "TASK-1", 0, "validated", {"ok": True}
            )
            repeated_intent = transaction.enqueue_projection(
                intent_key, "TASK-1", 0, "status", {"state": "pending"}
            )
        self.assertEqual(first_entry, repeated_entry)
        self.assertEqual(first_intent, repeated_intent)
        self.assertEqual(len(self.store.ledger_entries()), 1)
        self.assertEqual(len(self.store.projection_intents()), 1)

        with self.assertRaises(DeduplicationConflict):
            with self.store.transaction() as transaction:
                transaction.enqueue_projection(
                    intent_key, "TASK-1", 0, "status", {"state": "changed"}
                )
        self.assertEqual(len(self.store.projection_intents()), 1)

    def test_projection_ack_is_idempotent_and_does_not_repeat_action(self):
        self._create()
        intent_key = deterministic_key("outbox", "TASK-1", 0, "status")
        with self.store.transaction() as transaction:
            transaction.enqueue_projection(intent_key, "TASK-1", 0, "status")
        before = self.store.lifecycle("TASK-1")
        with self.store.transaction() as transaction:
            self.assertTrue(
                transaction.acknowledge_projection(intent_key, "provider-ack-1")
            )
        with self.store.transaction() as transaction:
            self.assertFalse(
                transaction.acknowledge_projection(intent_key, "provider-ack-1")
            )
        self.assertEqual(self.store.lifecycle("TASK-1"), before)
        self.assertEqual(len(self.store.ledger_entries()), 0)
        self.assertEqual(len(self.store.projection_acknowledgements()), 1)

    def test_transaction_failure_rolls_back_all_mutations(self):
        entry_key = deterministic_key("ledger", "TASK-1", 0, "validated")
        intent_key = deterministic_key("outbox", "TASK-1", 0, "status")
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with self.store.transaction() as transaction:
                record = transaction.compare_and_swap(
                    "TASK-1", None, IDENTITY, FakeSnapshot(IDENTITY, "pending")
                )
                transaction.claim_inbox_event("github", "delivery-1", "validate")
                transaction.append_ledger_entry(
                    entry_key, "TASK-1", record.version, "validated"
                )
                transaction.enqueue_projection(
                    intent_key, "TASK-1", record.version, "status"
                )
                raise RuntimeError("fixture failure")
        self.assertIsNone(self.store.lifecycle("TASK-1"))
        self.assertEqual(self.store.inbox_events(), ())
        self.assertEqual(self.store.ledger_entries(), ())
        self.assertEqual(self.store.projection_intents(), ())

    def test_failed_store_operation_poisons_transaction_even_when_caught(self):
        self._create()
        with self.store.transaction() as transaction:
            transaction.acquire_lease("TASK-1", "worker-a", 10)
            try:
                transaction.compare_and_swap(
                    "TASK-1", 99, IDENTITY, FakeSnapshot(IDENTITY, "review")
                )
            except StaleLifecycleVersion:
                pass
        self.assertIsNone(self.store.lease("TASK-1"))
        self.assertEqual(self.store.lifecycle("TASK-1").version, 0)

    def test_caught_validation_failures_always_roll_back_prior_mutations(self):
        invalid_operations = (
            lambda transaction: transaction.compare_and_swap(
                "TASK-1", True, IDENTITY, FakeSnapshot(IDENTITY, "review")
            ),
            lambda transaction: transaction.acquire_lease(
                "TASK-1", "worker-a", 0
            ),
        )
        for invalid_operation in invalid_operations:
            with self.subTest(operation=invalid_operation):
                store = InMemoryLifecycleStore(clock=self.clock)
                with store.transaction() as transaction:
                    transaction.compare_and_swap(
                        "TASK-1",
                        None,
                        IDENTITY,
                        FakeSnapshot(IDENTITY, "pending"),
                    )
                    with self.assertRaises(ValueError):
                        invalid_operation(transaction)
                self.assertIsNone(store.lifecycle("TASK-1"))
                self.assertIsNone(store.lease("TASK-1"))

    def test_event_action_failure_rolls_back_inbox_claim_for_retry(self):
        def failing_action(transaction):
            transaction.compare_and_swap(
                "TASK-1", None, IDENTITY, FakeSnapshot(IDENTITY, "pending")
            )
            raise RuntimeError("temporary failure")

        with self.assertRaises(RuntimeError):
            self.store.transact_inbox_event(
                "github", "delivery-1", "validate", failing_action
            )
        self.assertEqual(self.store.inbox_events(), ())
        self.assertIsNone(self.store.lifecycle("TASK-1"))


if __name__ == "__main__":
    unittest.main()
