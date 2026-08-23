from __future__ import annotations

from dataclasses import FrozenInstanceError
import tempfile
import unittest
from pathlib import Path

from orchestrator.universal_task_lifecycle import (
    AdapterStage,
    Authority,
    AuthorityBinding,
    CandidateBinding,
    EvidenceBinding,
    EvidenceResult,
    FailureKind,
    FixLoopBudget,
    LifecycleAction,
    LifecycleDenied,
    LifecycleEvidenceDenied,
    LifecycleSnapshot,
    LifecycleState,
    OrchestrationSnapshot,
    OrchestrationState,
    ReconciliationDecisionType,
    RetryClass,
    StageBinding,
    StageEvidence,
    TaskIdentity,
    apply_transition,
    apply_stage_evidence,
    authority_for,
    build_idempotency_key,
    classify_retry,
    decide_retry,
    evaluate_transition,
    intersect_authorities,
    reconcile,
    start_orchestration,
    validate_stage_evidence,
)
from orchestrator.universal_task_lifecycle_store import (
    InMemoryLifecycleStore,
    LifecycleIdentityMismatch,
)


def digest(character: str) -> str:
    return character * 64


class UniversalTaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.identity = TaskIdentity(
            task_id="TASK-106",
            project_id="ai-prof-control-center",
            task_sha256=digest("a"),
            source_sha="b" * 40,
        )
        self.authority = tuple(
            AuthorityBinding(action, Authority.AUTONOMOUS)
            for action in (
                LifecycleAction.VALIDATE,
                LifecycleAction.IMPLEMENT,
                LifecycleAction.AUDIT,
                LifecycleAction.FIX,
                LifecycleAction.TERMINATE,
            )
        )

    def evidence(self, kind: str, character: str = "c") -> EvidenceBinding:
        return EvidenceBinding(kind=kind, sha256=digest(character), task_id=self.identity.task_id)

    def stage_binding(self, *, attempt: int = 1, candidate: str = "2") -> StageBinding:
        return StageBinding(
            task=self.identity,
            validated_scope_sha256=digest("1"),
            candidate=CandidateBinding(
                base_sha="3" * 40,
                candidate_digest=digest(candidate),
            ),
            attempt=attempt,
            policy_version="policy-v1",
            evidence_version="evidence-v1",
        )

    def stage_evidence(
        self,
        stage: AdapterStage,
        result: EvidenceResult,
        binding: StageBinding,
        character: str,
        *,
        fingerprint: str | None = None,
    ) -> StageEvidence:
        return StageEvidence(
            stage,
            result,
            binding,
            digest(character),
            fingerprint,
        )

    def test_positive_legacy_lifecycle_returns_new_snapshots(self):
        pending = LifecycleSnapshot(identity=self.identity, authority=self.authority)
        review = apply_transition(
            pending,
            LifecycleState.REVIEW,
            evidence=(self.evidence("task_validated"),),
            expected_identity=self.identity,
        )
        implemented = apply_transition(
            review,
            LifecycleState.PENDING_CODEX,
            evidence=(
                self.evidence("implementation", "d"),
                self.evidence("required_checks", "e"),
            ),
            expected_identity=self.identity,
        )
        approved = apply_transition(
            implemented,
            LifecycleState.APPROVED,
            evidence=(self.evidence("stage_01c_pass", "f"),),
            expected_identity=self.identity,
        )

        self.assertEqual(pending.state, LifecycleState.PENDING)
        self.assertEqual(pending.version, 0)
        self.assertEqual(review.state, LifecycleState.REVIEW)
        self.assertEqual(implemented.state, LifecycleState.PENDING_CODEX)
        self.assertEqual(approved.state, LifecycleState.APPROVED)
        self.assertEqual(approved.version, 3)
        self.assertIs(approved.identity, self.identity)

    def test_identity_and_snapshots_are_immutable(self):
        snapshot = LifecycleSnapshot(identity=self.identity)
        with self.assertRaises(FrozenInstanceError):
            self.identity.source_sha = "0" * 40
        with self.assertRaises(FrozenInstanceError):
            snapshot.state = LifecycleState.APPROVED
        with self.assertRaises(ValueError):
            TaskIdentity("task", "project", "not-a-sha", "b" * 40)

    def test_missing_unknown_and_intersected_authority_fail_closed(self):
        self.assertEqual(intersect_authorities(), Authority.DENIED)
        self.assertEqual(intersect_authorities("unknown"), Authority.DENIED)
        self.assertEqual(
            intersect_authorities(Authority.AUTONOMOUS, Authority.OWNER_ONLY),
            Authority.OWNER_ONLY,
        )
        self.assertEqual(
            intersect_authorities(Authority.AUTONOMOUS, Authority.DENIED),
            Authority.DENIED,
        )
        self.assertEqual(authority_for(LifecycleAction.PUBLISH, {}), Authority.DENIED)
        self.assertEqual(authority_for("not-an-action", {}), Authority.DENIED)
        self.assertEqual(
            authority_for(LifecycleAction.PUBLISH, ("malformed",)),
            Authority.DENIED,
        )

        snapshot = LifecycleSnapshot(identity=self.identity)
        decision = evaluate_transition(
            snapshot,
            LifecycleState.REVIEW,
            evidence=(self.evidence("task_validated"),),
            expected_identity=self.identity,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.authority, Authority.DENIED)
        with self.assertRaises(LifecycleDenied):
            apply_transition(
                snapshot,
                LifecycleState.REVIEW,
                evidence=(self.evidence("task_validated"),),
                expected_identity=self.identity,
            )

    def test_owner_only_is_a_denial_for_autonomous_transition(self):
        snapshot = LifecycleSnapshot(
            identity=self.identity,
            authority=(AuthorityBinding(LifecycleAction.VALIDATE, Authority.OWNER_ONLY),),
        )
        decision = evaluate_transition(
            snapshot,
            LifecycleState.REVIEW,
            evidence=(self.evidence("task_validated"),),
            expected_identity=self.identity,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.authority, Authority.OWNER_ONLY)
        self.assertEqual(decision.reason, "owner action required")

    def test_illegal_transition_missing_evidence_and_identity_drift_are_denied(self):
        snapshot = LifecycleSnapshot(identity=self.identity, authority=self.authority)
        illegal = evaluate_transition(
            snapshot,
            LifecycleState.APPROVED,
            expected_identity=self.identity,
        )
        self.assertFalse(illegal.allowed)
        self.assertEqual(illegal.reason, "illegal transition")

        missing = evaluate_transition(
            snapshot,
            LifecycleState.REVIEW,
            expected_identity=self.identity,
        )
        self.assertFalse(missing.allowed)
        self.assertIn("task_validated", missing.reason)

        drifted = TaskIdentity("TASK-106", "other-project", digest("a"), "b" * 40)
        drift = evaluate_transition(
            snapshot,
            LifecycleState.REVIEW,
            evidence=(self.evidence("task_validated"),),
            expected_identity=drifted,
        )
        self.assertFalse(drift.allowed)
        self.assertIn("identity", drift.reason)

        foreign = EvidenceBinding("task_validated", digest("d"), "OTHER-TASK")
        foreign_decision = evaluate_transition(
            snapshot,
            LifecycleState.REVIEW,
            evidence=(foreign,),
            expected_identity=self.identity,
        )
        self.assertFalse(foreign_decision.allowed)
        self.assertIn("identity", foreign_decision.reason)

    def test_transition_rejects_unsupported_source_snapshot(self):
        unsupported_review = LifecycleSnapshot(
            identity=self.identity,
            state=LifecycleState.REVIEW,
            authority=self.authority,
            version=1,
        )
        next_edge_evidence = (
            self.evidence("implementation", "d"),
            self.evidence("required_checks", "e"),
        )

        decision = evaluate_transition(
            unsupported_review,
            LifecycleState.PENDING_CODEX,
            evidence=next_edge_evidence,
            expected_identity=self.identity,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.authority, Authority.DENIED)
        self.assertIn("source state lacks required evidence", decision.reason)
        self.assertIn("task_validated", decision.reason)

        with self.assertRaisesRegex(LifecycleDenied, "task_validated"):
            apply_transition(
                unsupported_review,
                LifecycleState.PENDING_CODEX,
                evidence=next_edge_evidence + (self.evidence("task_validated", "f"),),
                expected_identity=self.identity,
            )

    def test_idempotency_key_is_canonical_and_sensitive_to_immutable_inputs(self):
        first = self.evidence("implementation", "d")
        second = self.evidence("required_checks", "e")
        key_a = build_idempotency_key(
            self.identity,
            LifecycleState.REVIEW,
            LifecycleState.PENDING_CODEX,
            evidence=(first, second),
        )
        key_b = build_idempotency_key(
            self.identity,
            "review",
            "pending_codex",
            evidence=(second, first, first),
        )
        self.assertEqual(key_a, key_b)
        self.assertTrue(key_a.startswith("utl:v1:"))
        self.assertEqual(len(key_a), len("utl:v1:") + 64)
        self.assertNotEqual(
            key_a,
            build_idempotency_key(
                self.identity,
                LifecycleState.REVIEW,
                LifecycleState.PENDING_CODEX,
                evidence=(first, second),
                attempt=1,
            ),
        )

    def test_retry_classification_is_explicit_and_unknown_is_never(self):
        self.assertEqual(classify_retry(FailureKind.TIMEOUT), RetryClass.TRANSIENT)
        self.assertEqual(classify_retry(FailureKind.AUDIT_REJECTED), RetryClass.FIX_LOOP)
        self.assertEqual(
            classify_retry(FailureKind.AUTHORITY_DENIED),
            RetryClass.OWNER_ACTION_REQUIRED,
        )
        self.assertEqual(classify_retry("new-unclassified-failure"), RetryClass.NEVER)

    def test_fix_loop_budget_is_finite(self):
        budget = FixLoopBudget(max_fix_attempts=2, max_repeated_failures=5)
        first = decide_retry(
            FailureKind.AUDIT_REJECTED, budget, failure_fingerprint="audit-1"
        )
        second = decide_retry(
            FailureKind.AUDIT_REJECTED, first.budget, failure_fingerprint="audit-2"
        )
        exhausted = decide_retry(
            FailureKind.AUDIT_REJECTED, second.budget, failure_fingerprint="audit-3"
        )
        self.assertTrue(first.should_retry)
        self.assertTrue(second.should_retry)
        self.assertFalse(exhausted.should_retry)
        self.assertIn("exhausted", exhausted.reason)
        self.assertEqual(exhausted.budget.fix_attempts, 2)

    def test_repeated_failure_stops_before_fix_budget(self):
        budget = FixLoopBudget(max_fix_attempts=5, max_repeated_failures=2)
        first = decide_retry(
            FailureKind.CHECK_FAILED, budget, failure_fingerprint="same-failure"
        )
        repeated = decide_retry(
            FailureKind.CHECK_FAILED, first.budget, failure_fingerprint="same-failure"
        )
        self.assertTrue(first.should_retry)
        self.assertFalse(repeated.should_retry)
        self.assertIn("repeated-failure", repeated.reason)

    def test_reconciliation_decisions_are_deterministic_and_fail_closed(self):
        pending = LifecycleSnapshot(identity=self.identity, authority=self.authority)
        same = reconcile(pending, pending)
        self.assertEqual(same.decision, ReconciliationDecisionType.NO_ACTION)
        self.assertEqual(same, reconcile(pending, pending))

        observed_review = LifecycleSnapshot(
            identity=self.identity,
            state=LifecycleState.REVIEW,
            authority=self.authority,
            evidence=(self.evidence("task_validated"),),
            version=1,
        )
        advance = reconcile(
            pending, observed_review, action=LifecycleAction.VALIDATE
        )
        self.assertEqual(advance.decision, ReconciliationDecisionType.ADVANCE_SHADOW)
        self.assertEqual(advance.target_state, LifecycleState.REVIEW)

        missing_authority_context = reconcile(pending, observed_review)
        self.assertEqual(
            missing_authority_context.decision,
            ReconciliationDecisionType.BLOCK,
        )

        owner_publish = reconcile(
            pending,
            pending,
            action=LifecycleAction.PUBLISH,
            authority_layers=({LifecycleAction.PUBLISH: Authority.OWNER_ONLY},),
        )
        # Snapshot has no publish grant, so intersection remains denied.
        self.assertEqual(owner_publish.decision, ReconciliationDecisionType.BLOCK)

        owner_snapshot = LifecycleSnapshot(
            identity=self.identity,
            authority=(
                AuthorityBinding(LifecycleAction.PUBLISH, Authority.OWNER_ONLY),
            ),
        )
        self.assertEqual(
            reconcile(
                owner_snapshot,
                owner_snapshot,
                action=LifecycleAction.PUBLISH,
            ).decision,
            ReconciliationDecisionType.OWNER_ACTION_REQUIRED,
        )

        drifted_identity = TaskIdentity(
            "TASK-106", "ai-prof-control-center", digest("a"), "c" * 40
        )
        drifted = LifecycleSnapshot(identity=drifted_identity)
        self.assertEqual(
            reconcile(pending, drifted).decision,
            ReconciliationDecisionType.BLOCK,
        )

    def test_reconciliation_rejects_unsupported_snapshot_states(self):
        pending = LifecycleSnapshot(identity=self.identity, authority=self.authority)
        unsupported_review = LifecycleSnapshot(
            identity=self.identity,
            state=LifecycleState.REVIEW,
            authority=self.authority,
            version=1,
        )

        backward = reconcile(
            unsupported_review,
            pending,
            action=LifecycleAction.VALIDATE,
        )
        self.assertEqual(backward.decision, ReconciliationDecisionType.BLOCK)
        self.assertIn("shadow state lacks required evidence", backward.reason)

        same_unsupported_state = reconcile(unsupported_review, unsupported_review)
        self.assertEqual(
            same_unsupported_state.decision,
            ReconciliationDecisionType.BLOCK,
        )
        self.assertIn("task_validated", same_unsupported_state.reason)

    def test_reconciliation_shadow_ahead_requires_action_and_authority(self):
        pending = LifecycleSnapshot(identity=self.identity, authority=self.authority)
        supported_review = LifecycleSnapshot(
            identity=self.identity,
            state=LifecycleState.REVIEW,
            authority=self.authority,
            evidence=(self.evidence("task_validated"),),
            version=1,
        )

        self.assertEqual(
            reconcile(supported_review, pending).decision,
            ReconciliationDecisionType.BLOCK,
        )

        denied_review = LifecycleSnapshot(
            identity=self.identity,
            state=LifecycleState.REVIEW,
            authority=(),
            evidence=(self.evidence("task_validated"),),
            version=1,
        )
        denied = reconcile(
            denied_review,
            pending,
            action=LifecycleAction.VALIDATE,
        )
        self.assertEqual(denied.decision, ReconciliationDecisionType.BLOCK)
        self.assertIn("authority denied", denied.reason)

        keep = reconcile(
            supported_review,
            pending,
            action=LifecycleAction.VALIDATE,
        )
        self.assertEqual(keep.decision, ReconciliationDecisionType.KEEP_SHADOW)
        self.assertEqual(keep.target_state, LifecycleState.REVIEW)

    def test_model_calls_do_not_write_to_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            before = tuple(root.rglob("*"))
            snapshot = LifecycleSnapshot(identity=self.identity, authority=self.authority)
            evaluate_transition(
                snapshot,
                LifecycleState.REVIEW,
                evidence=(self.evidence("task_validated"),),
                expected_identity=self.identity,
            )
            build_idempotency_key(
                self.identity, LifecycleState.PENDING, LifecycleState.REVIEW
            )
            reconcile(snapshot, snapshot)
            self.assertEqual(tuple(root.rglob("*")), before)

    def test_slice3_exact_execute_test_audit_evidence_approves_candidate(self):
        binding = self.stage_binding()
        snapshot = start_orchestration(
            binding, FixLoopBudget(max_fix_attempts=2, max_repeated_failures=2)
        )
        executed = apply_stage_evidence(
            snapshot,
            self.stage_evidence(
                AdapterStage.EXECUTE, EvidenceResult.PASS, binding, "4"
            ),
        )
        tested = apply_stage_evidence(
            executed,
            self.stage_evidence(AdapterStage.TEST, EvidenceResult.PASS, binding, "5"),
        )
        approved = apply_stage_evidence(
            tested,
            self.stage_evidence(AdapterStage.AUDIT, EvidenceResult.PASS, binding, "6"),
        )
        self.assertEqual(approved.state, OrchestrationState.APPROVED)
        self.assertEqual(approved.binding.candidate.identity, binding.candidate.identity)
        self.assertEqual([item.stage for item in approved.evidence], list(AdapterStage))

    def test_slice3_missing_wrong_task_and_stale_candidate_fail_closed(self):
        binding = self.stage_binding()
        snapshot = start_orchestration(
            binding, FixLoopBudget(max_fix_attempts=1, max_repeated_failures=2)
        )
        with self.assertRaisesRegex(LifecycleEvidenceDenied, "missing or invalid"):
            validate_stage_evidence(snapshot, None)  # type: ignore[arg-type]

        foreign_task = TaskIdentity("OTHER", self.identity.project_id, digest("a"), "b" * 40)
        foreign = StageBinding(
            foreign_task,
            binding.validated_scope_sha256,
            binding.candidate,
            binding.attempt,
            binding.policy_version,
            binding.evidence_version,
        )
        with self.assertRaisesRegex(LifecycleEvidenceDenied, "mismatch"):
            apply_stage_evidence(
                snapshot,
                self.stage_evidence(
                    AdapterStage.EXECUTE, EvidenceResult.PASS, foreign, "7"
                ),
            )

        stale_candidate = self.stage_binding(candidate="8")
        with self.assertRaisesRegex(LifecycleEvidenceDenied, "mismatch"):
            apply_stage_evidence(
                snapshot,
                self.stage_evidence(
                    AdapterStage.EXECUTE,
                    EvidenceResult.PASS,
                    stale_candidate,
                    "8",
                ),
            )

        stale_base = StageBinding(
            binding.task,
            binding.validated_scope_sha256,
            CandidateBinding("4" * 40, candidate_digest=binding.candidate.candidate_digest),
            binding.attempt,
            binding.policy_version,
            binding.evidence_version,
        )
        with self.assertRaisesRegex(LifecycleEvidenceDenied, "mismatch"):
            apply_stage_evidence(
                snapshot,
                self.stage_evidence(
                    AdapterStage.EXECUTE, EvidenceResult.PASS, stale_base, "9"
                ),
            )

        stale_sha = StageBinding(
            binding.task,
            binding.validated_scope_sha256,
            CandidateBinding(binding.candidate.base_sha, candidate_sha="5" * 40),
            binding.attempt,
            binding.policy_version,
            binding.evidence_version,
        )
        with self.assertRaisesRegex(LifecycleEvidenceDenied, "mismatch"):
            apply_stage_evidence(
                snapshot,
                self.stage_evidence(
                    AdapterStage.EXECUTE, EvidenceResult.PASS, stale_sha, "0"
                ),
            )

    def test_slice3_wrong_attempt_and_audit_before_test_are_denied(self):
        binding = self.stage_binding()
        budget = FixLoopBudget(max_fix_attempts=2, max_repeated_failures=3)
        snapshot = start_orchestration(binding, budget)
        with self.assertRaisesRegex(LifecycleEvidenceDenied, "mismatch"):
            apply_stage_evidence(
                snapshot,
                self.stage_evidence(
                    AdapterStage.EXECUTE,
                    EvidenceResult.PASS,
                    self.stage_binding(attempt=2),
                    "9",
                ),
            )

        audit_first = OrchestrationSnapshot(
            binding,
            OrchestrationState.AUDIT,
            budget,
            evidence=(
                self.stage_evidence(
                    AdapterStage.EXECUTE, EvidenceResult.PASS, binding, "a"
                ),
            ),
        )
        with self.assertRaisesRegex(LifecycleEvidenceDenied, "TEST PASS"):
            apply_stage_evidence(
                audit_first,
                self.stage_evidence(
                    AdapterStage.AUDIT, EvidenceResult.PASS, binding, "b"
                ),
            )

    def test_slice3_identical_failure_and_fix_budget_are_finite(self):
        binding = self.stage_binding()
        repeated_budget = FixLoopBudget(max_fix_attempts=5, max_repeated_failures=2)
        snapshot = start_orchestration(binding, repeated_budget)
        snapshot = apply_stage_evidence(
            snapshot,
            self.stage_evidence(AdapterStage.EXECUTE, EvidenceResult.PASS, binding, "c"),
        )
        snapshot = apply_stage_evidence(
            snapshot,
            self.stage_evidence(
                AdapterStage.TEST,
                EvidenceResult.FAIL,
                binding,
                "d",
                fingerprint="identical-check-failure",
            ),
        )
        repaired = binding.next_attempt()
        snapshot = apply_stage_evidence(
            snapshot,
            self.stage_evidence(AdapterStage.EXECUTE, EvidenceResult.PASS, repaired, "e"),
        )
        snapshot = apply_stage_evidence(
            snapshot,
            self.stage_evidence(
                AdapterStage.TEST,
                EvidenceResult.FAIL,
                repaired,
                "f",
                fingerprint="identical-check-failure",
            ),
        )
        self.assertEqual(snapshot.state, OrchestrationState.FAILED)
        self.assertIn("repeated-failure", snapshot.reason)

        exhausted_budget = FixLoopBudget(max_fix_attempts=0, max_repeated_failures=3)
        exhausted = start_orchestration(binding, exhausted_budget)
        exhausted = apply_stage_evidence(
            exhausted,
            self.stage_evidence(AdapterStage.EXECUTE, EvidenceResult.PASS, binding, "0"),
        )
        exhausted = apply_stage_evidence(
            exhausted,
            self.stage_evidence(
                AdapterStage.TEST,
                EvidenceResult.FAIL,
                binding,
                "1",
                fingerprint="new-check-failure",
            ),
        )
        self.assertEqual(exhausted.state, OrchestrationState.FAILED)
        self.assertIn("exhausted", exhausted.reason)

    def test_slice3_audit_failure_repairs_same_lifecycle_then_retests(self):
        binding = self.stage_binding()
        snapshot = start_orchestration(
            binding, FixLoopBudget(max_fix_attempts=1, max_repeated_failures=3)
        )
        for stage, character in (
            (AdapterStage.EXECUTE, "4"),
            (AdapterStage.TEST, "5"),
        ):
            snapshot = apply_stage_evidence(
                snapshot,
                self.stage_evidence(stage, EvidenceResult.PASS, binding, character),
            )
        snapshot = apply_stage_evidence(
            snapshot,
            self.stage_evidence(
                AdapterStage.AUDIT,
                EvidenceResult.FAIL,
                binding,
                "6",
                fingerprint="stage-01c-rejection",
            ),
        )
        self.assertEqual(snapshot.state, OrchestrationState.FIX_LOOP)

        repaired = StageBinding(
            binding.task,
            binding.validated_scope_sha256,
            CandidateBinding(
                binding.candidate.base_sha, candidate_digest=digest("7")
            ),
            2,
            binding.policy_version,
            binding.evidence_version,
        )
        for stage, character in (
            (AdapterStage.EXECUTE, "8"),
            (AdapterStage.TEST, "9"),
            (AdapterStage.AUDIT, "a"),
        ):
            snapshot = apply_stage_evidence(
                snapshot,
                self.stage_evidence(stage, EvidenceResult.PASS, repaired, character),
            )
        self.assertEqual(snapshot.state, OrchestrationState.APPROVED)
        self.assertEqual(snapshot.binding, repaired)
        self.assertEqual(snapshot.binding.task, binding.task)
        self.assertEqual(
            snapshot.binding.validated_scope_sha256,
            binding.validated_scope_sha256,
        )
        self.assertEqual(
            snapshot.binding.candidate.base_sha, binding.candidate.base_sha
        )

    def test_slice3_store_rejects_cross_task_stage_evidence(self):
        binding = self.stage_binding()
        evidence = self.stage_evidence(
            AdapterStage.EXECUTE, EvidenceResult.PASS, binding, "2"
        )
        lifecycle = LifecycleSnapshot(identity=self.identity)
        store = InMemoryLifecycleStore()
        with store.transaction() as transaction:
            record = transaction.compare_and_swap(
                self.identity.task_id, None, self.identity, lifecycle
            )
            stored = transaction.append_stage_evidence(
                "execute-evidence", self.identity.task_id, record.version, evidence
            )
        self.assertEqual(stored.evidence, evidence)
        self.assertEqual(store.stage_evidence()[0], stored)

        foreign_identity = TaskIdentity(
            "OTHER", self.identity.project_id, digest("a"), "b" * 40
        )
        foreign_binding = StageBinding(
            foreign_identity,
            binding.validated_scope_sha256,
            binding.candidate,
            binding.attempt,
            binding.policy_version,
            binding.evidence_version,
        )
        foreign_evidence = self.stage_evidence(
            AdapterStage.EXECUTE, EvidenceResult.PASS, foreign_binding, "3"
        )
        with self.assertRaises(LifecycleIdentityMismatch):
            with store.transaction() as transaction:
                transaction.append_stage_evidence(
                    "wrong-key",
                    self.identity.task_id,
                    record.version,
                    foreign_evidence,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
