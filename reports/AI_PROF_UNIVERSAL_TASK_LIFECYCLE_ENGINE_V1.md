# AI PROF Universal Task Lifecycle Engine Architecture Contract V1

**Status:** implementation-ready architecture contract; documentation only
**Task:** `AI_PROF_CONTROL_CENTER_20260823T045559Z_9D82E0` / GitHub issue `#102`
**Scope:** transition from Stage-01C-approved manual publication to policy-driven task completion without granting new authority
**Normative language:** **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are requirements in the RFC 2119 sense.

## 1. Purpose and non-goals

This contract defines one durable, fail-closed lifecycle for AI PROF tasks:

`TASK -> VALIDATE -> EXECUTE -> TEST -> AUDIT -> FIX_LOOP -> APPROVED -> PUBLISH -> PR -> MERGE -> COMPLETE`

The engine coordinates existing validation, sandboxed execution, tests, Stage 01C audit, approved publication, GitHub issue and pull-request state, and terminal queue state. It does not collapse these controls into a single privileged process. Each transition is allowed only by a versioned policy decision and evidence bound to the exact task, repository, branch, base SHA, and candidate SHA.

This document grants no runtime authority. In particular, it does not enable commits, pushes, merges, deployments, secrets access, database access, destructive actions, financial or legal actions, or production changes. Existing false capability flags and owner gates remain controlling until a separately scoped, owner-approved code and policy change is implemented and reviewed.

## 2. Repository evidence baseline and current blockers

### 2.1 Direct evidence ledger

The following evidence identifiers are the basis for current-state claims in this contract. The locators deliberately use stable JSON keys, Python integration seams, and test names rather than line numbers, which would become stale as code moves.

| ID | Repository file and stable locator | Direct architectural evidence | Contract consequence |
|---|---|---|---|
| **E1** | `orchestrator/projects.json` — AI PROF Control Center self-maintenance project entry; keys `allow_commits`, `allow_push`, and `allow_merge` | All three capability values are `false`. The project profile therefore withholds commit, push, and merge authority from AI PROF self-maintenance. | `PUBLISH`, remote branch/PR publication, and `MERGE` cannot be enabled merely by adding lifecycle code. A false or missing capability is a hard denial. |
| **E2** | `tests/test_self_maintenance_profile.py` — self-maintenance capability/profile assertions | The test protects the AI PROF self-maintenance profile as a restricted profile, including the false commit, push, and merge capabilities recorded in E1. | Lifecycle work must preserve those denials and add regression coverage rather than change the fixture or weaken its assertions. |
| **E3** | `orchestrator/control_loop_service.py` — imports, publisher construction, and approved-task dispatch path | The control loop has no AI PROF approved-task publisher import, construction, or project dispatch branch. Its current dispatch surface therefore does not route an approved AI PROF self-maintenance task to publication. | Stage 01C approval cannot be treated as an implicit call to a generic publisher. An explicit, separately reviewed AI PROF adapter/route is required before even a denied/dry-run publication evaluation can be wired into the loop. |
| **E4** | `orchestrator/approved_task_publisher_gate.py` — approved-task publisher gate boundary | A generic approved-task publication gate exists as a distinct security boundary rather than publication being an executor side effect. | The lifecycle must call publication only through a project-authorized gate after `APPROVED`; it must not place Git mutations in `EXECUTE`, `TEST`, or `AUDIT`. |
| **E5** | `orchestrator/ak_bermet_approved_task_publisher_gate.py` — AK BERMET-specific gate and project binding | The repository has a project-specific AK BERMET publication boundary in addition to the generic gate. This is concrete evidence that project binding is explicit, not inferred from the presence of a generic publisher. | A future AI PROF integration must be explicit and least-privilege. The AK BERMET path is evidence for separation, not reusable authority for AI PROF. |
| **E6** | `orchestrator/TASK_SCHEMA.md` — validated task contract and approval/publication vocabulary | The existing task schema is the compatibility boundary for validated task data supplied to orchestration. It does not itself grant a project a missing commit, push, merge, or publisher capability. | Lifecycle fields must be backward-compatible additions, and unknown authority/lifecycle values must fail closed. Task prose cannot override project policy. |
| **E7** | `tests/test_control_loop.py` — control-loop routing/behavior tests | The control-loop test surface is the required regression location for publisher selection and no-side-effect behavior. Together with the dispatch inspection in E3, it exposes the absence of an AI PROF publisher route as an implementation gap, not an authorization gap that may be bypassed. | A future route must be covered by positive project selection and negative missing/disabled-capability tests; existing loop behavior must remain unchanged in the pure-model slice. |

### 2.2 Blockers established by the evidence

1. **AI PROF lacks publication and merge capability.** The AI PROF self-maintenance entry explicitly sets `allow_commits=false`, `allow_push=false`, and `allow_merge=false` [E1], and the dedicated profile test protects the restricted profile [E2]. Consequently, the current system cannot autonomously cross `PUBLISH`, create/update a remote work branch for `PR`, or cross `MERGE`. The engine MUST return `OWNER_ACTION_REQUIRED` for those actions and MUST NOT reinterpret Stage 01C approval, task urgency, a publisher's existence, or missing policy as authority.
2. **AI PROF has no control-loop publisher route.** The control loop contains no AI PROF publisher import, instance, or dispatch branch [E3]. The presence of a generic publisher gate [E4] does not create a route, and the separate AK BERMET gate demonstrates explicit project binding [E5]. Thus an approved AI PROF task presently has no control-loop transition into an AI PROF publication adapter. Adding that route is a future owner-approved implementation change, and the route must still deny publication while E1 remains unchanged.
3. **Approval and publication are separate boundaries.** The task-schema boundary [E6], generic publisher boundary [E4], and project-specific publisher boundary [E5] require the lifecycle to preserve Stage 01C evidence separately from publication authority. `APPROVED` is eligibility for a publisher decision; it is never permission to commit, push, create a PR, merge, or deploy.
4. **The missing route is not permission to reuse another project's gate.** The AK BERMET-specific gate is bound to AK BERMET [E5]. AI PROF must not select it as a fallback, copy its authority, or infer that any passed AK BERMET evidence applies to AI PROF.

These evidence-backed restrictions are invariants of V1. If a future implementation observes a conflicting repository state, it MUST stop fail-closed and require an owner-reviewed contract/policy update; it MUST NOT silently broaden the profile or publisher selection.

## 3. Security and authority model

### 3.1 Authority is explicit, stage-specific, and deny-by-default

Every project-operation-stage tuple MUST resolve to one of these levels:

| Level | Meaning | Permitted engine behavior |
|---|---|---|
| `AUTO` | A versioned owner policy explicitly authorizes this bounded transition without per-task intervention. | Execute the transition only inside validated scope and only when all preconditions and evidence checks pass. |
| `AUTO_WITH_GATES` | Automation is authorized to prepare or attempt the transition after specified machine and/or owner gates produce signed or otherwise tamper-evident evidence. | Wait for every named gate, bind its evidence to exact immutable inputs, then act. A gate timeout, ambiguity, or mismatch is a denial. |
| `OWNER_ONLY` | A human owner must perform or explicitly authorize the exact action. | Prepare evidence and a bounded action request, set `OWNER_ACTION_REQUIRED`, and perform no side effect. |

Authority MUST be computed as the most restrictive result of:

- platform hard limits;
- project profile;
- operation profile;
- validated task contract and allowed scope;
- lifecycle-stage policy;
- risk classification;
- current repository and branch protections;
- any applicable owner approval, including its action, target, expiry, and one-use constraints.

No task text, GitHub label, urgency, previous approval, successful earlier task, publisher availability, or service configuration may raise authority. Unknown fields, unknown operations, policy-load errors, contradictory policies, expired approvals, and absent policy all resolve to `OWNER_ONLY` or `BLOCKED`, never `AUTO`.

### 3.2 Default stage authority

The following is an upper bound, not an enabling policy:

| Stage | Maximum default level | Notes |
|---|---|---|
| `TASK`, `VALIDATE` | `AUTO` | Read and validate already-authorized task data; no scope expansion or runtime mutation. |
| `EXECUTE`, `TEST`, `AUDIT`, `FIX_LOOP` | `AUTO_WITH_GATES` | Only in an isolated workspace, within explicit Scope-Files, using allowlisted tools/checks and bounded retries. |
| `APPROVED` | `AUTO_WITH_GATES` | Requires fresh TEST and Stage 01C audit evidence for the exact candidate. It is a recorded state, not authority to publish. |
| `PUBLISH`, `PR`, `MERGE` | `OWNER_ONLY` | Remain owner-only unless separate policy explicitly authorizes the exact stage and the project capability flags permit it. Current AI PROF false flags continue to deny these actions. |
| `COMPLETE` | `AUTO` | May reconcile already-proven terminal facts; MUST NOT manufacture success or perform a denied upstream action. |

### 3.3 Permanently high-risk classes

Production deployment or activation, secret or credential access/change, database reads or writes not explicitly scoped, migrations, destructive operations, service control, broad filesystem access, financial actions, legal actions, security-policy changes, authorization changes, project-registry changes, and other high-risk operations MUST remain `OWNER_ONLY` unless each class is separately and explicitly authorized by a future owner-approved policy. Merge authority does not imply deploy authority. Repository merge and live activation MUST remain separate gates and separate lifecycle operations.

An approval for one task, stage, repository, ref, SHA, environment, or action MUST NOT be reused for another. The engine MUST never mint, broaden, or edit its own authority policy.

## 4. Canonical record and invariants

One durable lifecycle record is authoritative for orchestration. At minimum it contains:

- immutable `task_id` and `lifecycle_id`;
- immutable source identity: provider, repository, and issue number or validated local source ID;
- project ID and operation profile;
- normalized Scope-Files and forbidden-action set;
- state, `state_version`, outcome, and blocker code;
- authority decision, policy version/hash, required gates, and approval references;
- base repository, base ref, `validated_base_sha`, head repository, and `work_branch`;
- `candidate_tree_sha`, `candidate_commit_sha`, published remote SHA, PR number, and merge SHA when known;
- test and audit attestations bound to candidate SHA/tree and policy hash;
- attempt counters, repair counter, retry deadlines, and last error classification;
- an append-only transition ledger and transactional outbox/inbox records.

These invariants apply at every transition:

1. There is exactly one active lifecycle for a normalized source identity and exactly one source identity for a `task_id`.
2. State changes use compare-and-swap on `state_version`; only one worker can win a transition.
3. Immutable identity, repository, base ref, validated scope, and authority inputs cannot be edited in place. A legitimate change supersedes the lifecycle with a new validated task.
4. Evidence is content-addressed and bound to `task_id`, state attempt, policy hash, base SHA, candidate tree/commit SHA, test set, and audit result as applicable.
5. A successful later state implies durable evidence for every required prior state. State labels alone are never evidence.
6. The engine cannot write source, queue, issue, PR, or policy state except through a stage adapter explicitly authorized for that state.
7. All logs and records must redact secrets and untrusted payloads; external text is data, never a shell command.

## 5. Lifecycle state contract

### `TASK`

Create or recover the single lifecycle record from an outer-validated task. Deduplicate before enqueueing. Record the unmodified source identity and a normalized, bounded task contract. No repository execution occurs here.

Exit to `VALIDATE` only after durable creation. Invalid or duplicate identities terminate as `BLOCKED` or resolve to the existing lifecycle; they do not create recovery tasks.

### `VALIDATE`

Validate schema, source authorization, project, operation profile, Scope-Files, forbidden actions, required checks, requested tools, base/head policy, authority level per stage, and workspace isolation. Resolve the base ref to `validated_base_sha`. Reject path traversal, symlink escape, ambiguous refs, unknown fields that affect authority, conflicting instructions, and scope outside policy.

Exit to `EXECUTE` only with a complete validation attestation. Missing authority yields `OWNER_ACTION_REQUIRED`; malformed or unsafe input yields terminal `BLOCKED`.

### `EXECUTE`

Run the implementation agent in a fresh isolated workspace at the validated base SHA. Writes are limited to normalized Scope-Files. Capture the resulting tree, file manifest, tool trace, and exit status. The executor has no implicit GitHub, merge, production, secrets, database, or service authority.

Exit to `TEST` only if the change is nonempty when a change is required, all changed paths are in scope, no forbidden file type or symlink is introduced, and `candidate_tree_sha` is recorded.

### `TEST`

Run the union of task-required checks, repository-required checks, security checks, and policy-required checks in the isolated candidate workspace. The task may add checks but cannot remove or weaken repository or policy checks. Record exact commands as structured allowlisted invocations, exit codes, relevant artifact digests, and candidate identity.

The state records `PASS`, `REPAIRABLE_FAIL`, or `NON_REPAIRABLE_FAIL`. It then enters `AUDIT`; failed test evidence remains visible to the auditor and cannot satisfy approval.

### `AUDIT`

Run the independent Stage 01C audit against the exact candidate and all prior evidence. The auditor MUST be unable to edit the candidate, policy, queue, or evidence. It checks scope, task satisfaction, test integrity, security invariants, provenance, and authority.

Record `PASS`, `REPAIRABLE_FAIL`, or `NON_REPAIRABLE_FAIL`. Every attempt proceeds to `FIX_LOOP`, which is the sole decision point for repair versus approval.

### `FIX_LOOP`

This state evaluates TEST and AUDIT outcomes:

- If both passed for the same candidate and policy version, record zero repair action and proceed to `APPROVED`.
- If either produced a repairable failure and the repair budget remains, create a bounded repair instruction in the same lifecycle, increment `repair_count`, invalidate candidate-bound TEST/AUDIT evidence, and return to `EXECUTE` in a clean workspace.
- If the failure is non-repairable, authority-related, unchanged across the configured repetition limit, outside scope, or over budget, stop as `BLOCKED` or `OWNER_ACTION_REQUIRED`.

The default repair budget MUST be finite and policy-defined. It MUST NOT be raised by task prose. A repair may not expand scope, change base/ref policy, weaken checks, alter authorization, or convert an owner-only action into an automatic action.

### `APPROVED`

Record an immutable approval envelope only when fresh TEST and AUDIT passes bind to the same candidate tree/commit, validated base, scope, and policy. Approval becomes stale on any content change, base change requiring revalidation, policy change, check-set change, or approval expiry.

`APPROVED` means eligible for evaluation by a publisher gate. It does not itself permit commit, push, PR creation, merge, or deployment.

### `PUBLISH`

Invoke the project-specific approved-task publisher only if publication authority and project capabilities allow it. The publisher must revalidate the approval envelope, changed-path manifest, base/head invariants, and current remote observations. It may create the one deterministic candidate commit and push only the one validated work branch with compare-and-swap protection.

For current AI PROF self-maintenance, `allow_commits=false` and `allow_push=false` [E1, E2] force `OWNER_ACTION_REQUIRED`; the control loop has no AI PROF publisher route [E3] and must not substitute the AK BERMET gate [E5]. A future route and any capability change are separate owner-approved changes.

### `PR`

Ensure exactly one pull request binds the validated head repository/branch to the validated base repository/ref. Create it only if no matching open or merged PR exists. Otherwise adopt the unique exact match after verifying its task marker, head SHA, head/base repos, refs, and immutable lifecycle identity. Synchronize evidence and machine-managed status without overwriting owner-authored issue or PR text.

### `MERGE`

Re-evaluate authority immediately before merge. Require current head SHA equal to the approved published SHA, current base/ref policy, all required GitHub branch protections and checks passing for that SHA, non-stale TEST/AUDIT approval, no blocking review/change request, and the configured merge method. Use expected-head-SHA protection where supported.

Current `allow_merge=false` [E1, E2] forces `OWNER_ACTION_REQUIRED`. A merge, even when later authorized, grants no production or deployment authority.

### `COMPLETE`

Enter only after independently observing the required terminal fact: for a PR workflow, the unique PR is merged and its merge result matches the expected task/head; for a non-PR workflow explicitly allowed by policy, its equivalent terminal evidence exists. Then reconcile the queue and issue to terminal success using the synchronization rules below. A closed issue alone, a successful check alone, or an attempted merge is not completion.

Terminal outcomes are `COMPLETE`, `BLOCKED`, `OWNER_ACTION_REQUIRED`, `CANCELLED`, and `SUPERSEDED`. Only `COMPLETE` is success.

## 6. Fail-closed transition rules

A transition MUST NOT run when any of these conditions exists:

- missing, malformed, contradictory, stale, unverifiable, or more permissive policy/evidence;
- unknown current state or illegal transition;
- state-version conflict or concurrent lease uncertainty;
- source identity, project, repository, issue, branch, base, SHA, scope, or approval mismatch;
- uncommitted/unattributed workspace changes or out-of-scope paths;
- unavailable mandatory test, audit, publisher, GitHub, or persistence service;
- required check skipped, neutral, cancelled, timed out, stale, or attached to another SHA;
- remote branch movement not made by the lifecycle's acknowledged publish operation;
- more than one plausible issue, branch, PR, commit, or lifecycle match;
- partial failure whose external result cannot be proven;
- secret, production, database, destructive, legal, financial, or other high-risk action without specific owner authority.

On denial, the engine records a machine-readable blocker and the observed evidence. It performs no compensating destructive action. `OWNER_ACTION_REQUIRED` is used only where an owner can safely resolve a policy/approval decision; unsafe or inconsistent state is `BLOCKED`.

## 7. Idempotency, concurrency, and retry semantics

### 7.1 Idempotency keys

Every transition uses:

`task_id : from_state : from_state_version : candidate_or_base_sha : policy_hash`

Every external side effect adds its kind and immutable target, for example:

- publish commit: `... : commit : candidate_tree_sha`
- branch push: `... : push : repository : work_branch : expected_old_sha : candidate_commit_sha`
- PR ensure: `... : pr : head_repository : work_branch : base_repository : base_ref`
- merge: `... : merge : pr_number : expected_head_sha : merge_method`
- issue/queue update: `... : projection : destination : desired_state_version`

The durable idempotency result is checked before execution and stored atomically with the outbox intent. A repeated call returns the established result after re-observation; it does not repeat the side effect blindly.

### 7.2 Concurrency

Workers use a bounded lease plus compare-and-swap. Lease expiry permits another worker to reconcile, not assume failure. Only the successful state-version writer may enqueue the next transition. External observations never bypass the state machine; they generate inbox events deduplicated by provider event ID and content digest.

### 7.3 Retry classification

- Transient failures (timeouts, rate limits, temporary service errors) retry the same idempotent operation with bounded exponential backoff and jitter.
- Conflict failures (SHA movement, duplicate match, policy change, state-version conflict) do not retry the mutation; they re-observe and either reconcile an exact success or block.
- Validation, authorization, scope, test, and audit failures are not transport retries. Only `FIX_LOOP` may request a bounded repair.
- Permanent external failures stop as `BLOCKED`; missing authority stops as `OWNER_ACTION_REQUIRED`.

All retry counts and elapsed-time ceilings are policy-defined and finite. Exhaustion never creates a new task automatically.

## 8. Queue, GitHub issue, and PR synchronization

### 8.1 Sources of truth

- The validated lifecycle record is authoritative for execution state, evidence, attempts, and next allowed transition.
- The GitHub issue is authoritative for owner-authored request discussion and explicit owner decisions, but labels or prose alone do not grant authority.
- The Git commit graph and remote ref are authoritative for published SHAs.
- The pull request and its provider-side checks/reviews are authoritative for PR open/closed/merged facts.
- Project policy is authoritative for capability and gate decisions.

No projection may overwrite another system's authoritative facts.

### 8.2 Identity and uniqueness

The queue enforces unique `(source_provider, source_repository, source_issue_number)` and unique `task_id`. The issue stores a machine marker containing only the lifecycle ID and task ID. The work branch and commit metadata carry the task ID. The PR carries one machine marker and the exact head/base tuple.

A PR may be adopted only when all identifiers and SHAs agree. Title similarity, body similarity, branch-name similarity, or issue backlinks alone are insufficient. Zero matches permits creation when authorized; one exact match permits adoption; multiple or conflicting matches block.

### 8.3 Projection order

For each state change:

1. Atomically persist the new lifecycle state/ledger entry and one outbox intent per projection.
2. Project the queue's user-visible state with compare-and-swap on lifecycle version.
3. Re-observe GitHub before any GitHub mutation.
4. Apply the issue projection using a single machine-owned status block/comment key; preserve owner text and comments.
5. Apply the PR projection only when a PR exists.
6. Record provider IDs, returned versions/ETags when available, and normalized observed state; mark the outbox item complete.

Projection failure does not roll back a proven lifecycle or Git fact. It leaves a pending outbox item for reconciliation and cannot cause the underlying action to repeat.

### 8.4 Exact state projections

| Lifecycle condition | Queue | Issue | PR |
|---|---|---|---|
| Active `TASK` through `FIX_LOOP` | Active state, attempt, no success | One upserted machine status with state and safe blocker summary | None unless already discovered; if discovered early, record and block unexpected state |
| `APPROVED` | Approved, awaiting authority/publish | Approved for the bound SHA; explicitly not complete | None or exact existing PR only |
| `PUBLISH` complete | Published SHA | Published SHA and next stage | None or exact existing PR |
| `PR` complete | PR number and head SHA | Link exact PR | Machine status/check binds lifecycle and approved SHA |
| `OWNER_ACTION_REQUIRED` | Paused with action code | One deduplicated owner-action status | Non-destructive status only |
| `BLOCKED` | Terminal blocked with reason code | One deduplicated blocked status | Non-destructive status only |
| `COMPLETE` | Terminal complete with merge/terminal SHA | Final success status; close issue only if policy explicitly says this machine owns closure | Merged fact observed; no further mutation required |

The engine MUST NOT mark the queue or issue complete before the terminal GitHub fact is observed. It MUST NOT reopen an owner-closed issue automatically. Owner closure before completion pauses or cancels according to policy; it never fabricates `COMPLETE`. Owner edits to labels/text cannot move the lifecycle backward or forward without a validated event and legal transition.

## 9. Branch, base, commit, and SHA invariants

1. Repository, base ref, and work-branch policy are resolved during `VALIDATE` from trusted project policy, not issue prose.
2. `validated_base_sha` is immutable for an execution attempt. A changed base is either allowed to continue by explicit policy or causes a new validation/execution/test/audit cycle; it cannot reuse stale approval.
3. The work branch is deterministic and unique to `task_id`, or an exact pre-existing policy-approved branch is bound during validation. It cannot be a protected base branch.
4. Before publish, an absent remote work branch is acceptable. An existing one is acceptable only if its SHA is a previously acknowledged SHA for the same lifecycle. Any other SHA blocks; no force push or branch deletion is permitted.
5. The candidate commit has the validated tree, allowed parent(s), machine-readable task marker, and no out-of-scope changes. Retrying publication discovers/reuses this exact commit rather than creating commit spam.
6. Push is compare-and-swap from the observed expected old SHA to `candidate_commit_sha`. Force push is forbidden by default.
7. A PR's head repository/ref/SHA and base repository/ref MUST exactly match the lifecycle. A PR retarget, unexpected head update, or cross-repository mismatch invalidates approval and blocks automatic merge.
8. TEST, AUDIT, approval, CI, and merge all bind the exact candidate/published head SHA. Equivalent-looking patches or later commits do not inherit evidence.
9. Merge uses expected head SHA and records the provider's merge SHA/result. `COMPLETE` independently re-reads the PR and refs; an API success response alone is insufficient.

## 10. Test, CI, and audit requirements

The required check set is the monotonic union of repository branch protections, repository configuration, validated task `Required-Checks`, operation profile, and lifecycle security checks. A lower-trust source cannot subtract a check. For this task's stated future baseline, `python3 -m unittest` remains required unless current repository policy adds stricter checks.

For a candidate to reach `APPROVED`:

- all required checks run after the last content change and against the exact candidate;
- each required result is an unambiguous pass; skipped, missing, stale, cancelled, neutral, and timed-out results fail closed;
- test discovery and selection cannot be modified outside explicit scope, and changes to tests or CI receive heightened audit scrutiny;
- Stage 01C runs independently after tests and produces a pass bound to the same candidate and evidence set;
- no audit finding is suppressed by the executor; repair invalidates all prior candidate-bound passes;
- publisher rechecks evidence integrity before commit/push;
- merge rechecks provider CI/branch protection, required reviews, expected head SHA, and approval freshness.

Tests MUST exercise denial paths at least as strongly as success paths. No implementation slice may weaken `tests/test_self_maintenance_profile.py`, `tests/test_control_loop.py`, authentication, authorization, RLS, redaction, sandboxing, or fail-closed behavior.

## 11. Partial-failure recovery

Recovery always begins by reading durable intent and independently observing the external system. It never guesses and never deletes or rewrites evidence to make states agree.

| Failure window | Required recovery |
|---|---|
| Candidate commit created locally, record not updated | Recompute tree/parents/marker. Adopt only the exact deterministic commit; otherwise block. Do not create a second commit until nonexistence is proven. |
| Commit recorded, push not known | Read remote ref. If it equals candidate SHA, record publish success. If it equals expected old SHA, retry compare-and-swap push. Any other SHA blocks. |
| Push succeeded, lifecycle/queue update failed | Read remote ref and verify task commit. Adopt exact success and replay projections. Never push again merely because the queue is stale. |
| PR create timed out | Search by exact head repo/ref plus lifecycle marker and verify base/head SHAs. Adopt one exact PR, create only if absence is proven, block on ambiguity. |
| PR exists, issue/queue link failed | Record the verified PR and replay only projection outbox items. Do not create another PR or issue. |
| Merge request timed out | Re-read PR. If merged with expected head and allowed result, record merge success. If still open at the same head, retry only within merge policy. If closed/unmerged or changed, block. |
| Merge succeeded, terminal sync failed | Verify merged fact and merge SHA, enter/recover `COMPLETE`, and replay queue/issue projections. Do not re-merge or open a recovery PR. |
| Issue update succeeded, acknowledgement lost | Re-read the machine-owned marker/version. Adopt exact projection or update it in place. Never append duplicate status comments. |
| Policy or base changes during an attempt | Stop mutations, invalidate affected evidence, and return to the earliest stage required by policy. Never grandfather an in-flight task automatically. |

Compensation is forward reconciliation. Automatic force pushes, branch deletion, PR closure, issue reopening, history rewrite, and rollback of merged code are prohibited unless a separate owner-approved task explicitly authorizes the exact action.

## 12. Duplicate and recovery-task spam prevention

1. Intake deduplicates on normalized source identity and task ID before creating a lifecycle.
2. Delivery/webhook duplicates deduplicate on provider event ID and payload digest.
3. Status updates use one upsertable machine-owned projection, not a comment per poll/retry.
4. Publication reuses the deterministic task commit; PR reconciliation adopts one exact PR.
5. Repair remains an attempt within the same lifecycle and increments `repair_count`; it is not a new queue task or GitHub issue.
6. A recovery record, if operationally required, is a child record under the same lifecycle and cannot itself enter normal intake.
7. At most one unresolved owner-action notice exists per `(task_id, blocker_code, blocker_fingerprint)`. Repeated observations update its timestamp/count without notifying again until a policy-defined cooldown or material fingerprint change.
8. Terminal tasks are not automatically reopened or cloned. Restart requires an explicit validated owner action and normally creates a superseding task linked to the original.
9. Repeated identical TEST/AUDIT failure fingerprints stop repair before the overall budget is exhausted according to a finite repetition limit.
10. Reconciliation scans repair existing records only; it never creates tasks merely because a projection is missing.

## 13. Minimal implementation slices

Implementation must be incremental and must preserve current manual publication until each later slice is separately approved.

### Slice 1: pure lifecycle model in shadow mode

Add a deterministic state machine, typed records, transition validation, authority calculation interface, idempotency keys, and reconciliation decisions. Feed it copied/test fixtures only. It performs no GitHub, queue, Git, publisher, or runtime writes. This establishes denial behavior and schema compatibility first.

### Slice 2: durable ledger and transactional projections

Add compare-and-swap state persistence, leases, inbox deduplication, and outbox projections behind interfaces. Initially use test fakes and a read-only/shadow control-loop integration. Do not mutate production queues or GitHub.

### Slice 3: execution/test/audit orchestration

Connect existing isolated executor, required-check runner, and Stage 01C audit as separate least-privilege adapters. Enforce Scope-Files and evidence/SHA binding. Retain finite fix-loop budgets and no publication authority.

### Slice 4: AI PROF approved publisher routing

After owner review, add an explicit AI PROF route in `control_loop_service.py` to the appropriate `approved_task_publisher_gate.py` implementation, mirroring only proven fail-closed patterns from the AK BERMET gate where applicable. The route must remain disabled by the existing `allow_commits=false`, `allow_push=false`, and `allow_merge=false` profile and must have denial tests. Merely wiring a publisher does not enable it.

### Slice 5: staged publication and PR reconciliation

Under separate owner approval, permit narrowly scoped commit/push/PR actions one at a time, protected by SHA compare-and-swap and idempotent reconciliation. Keep merge owner-only. Prove partial-failure recovery and duplicate prevention before enabling each action.

### Slice 6: optional autonomous merge policy

Only a separate owner-approved security/policy task may consider `AUTO_WITH_GATES` merge. It must retain expected-head-SHA, CI, review, branch-protection, audit-freshness, and capability checks. Production deployment remains a separate owner-only workflow regardless of merge policy.

## 14. Next owner-approved code task: exact minimal plan

The next code task should implement **Slice 1 only**, in this order:

1. Treat E1–E7 as acceptance invariants: record their exact current values/dispatch results in the code-task audit evidence, and stop if repository drift contradicts them. This is a drift check, not deferred support for the present contract.
2. Update `orchestrator/TASK_SCHEMA.md` with the lifecycle states, terminal outcomes, immutable identity/SHA fields, authority enum, evidence bindings, legal transitions, and backward-compatible defaults. Unknown authority values must fail closed.
3. Add `orchestrator/universal_task_lifecycle.py` containing a side-effect-free state machine, authority intersection, transition precondition checks, idempotency-key construction, retry classification, fix-loop budget logic, and reconciliation decision types. It must expose no shell, GitHub, queue, Git, filesystem-write, or publisher calls.
4. Add `tests/test_universal_task_lifecycle.py` covering every legal transition and illegal skip; most-restrictive authority; missing/unknown policy denial; stale or mismatched SHA/evidence denial; finite and repeated-failure fix-loop stopping; deterministic idempotency keys; retry classification; duplicate identity/PR ambiguity decisions; and partial publish/merge observation decisions as pure data.
5. Extend `tests/test_self_maintenance_profile.py` only to assert that lifecycle introduction leaves AI PROF `allow_commits`, `allow_push`, and `allow_merge` false and maps `PUBLISH`/`PR`/`MERGE` to `OWNER_ONLY` or denied.
6. Extend `tests/test_control_loop.py` only with a regression assertion that Slice 1 performs no lifecycle side effects and does not invent an AI PROF publisher route.
7. Run `python3 -m unittest`; then run the existing Stage 01C audit over exactly the owner-scoped files. Any failure blocks the slice.

Exact proposed files for that code task:

- `orchestrator/TASK_SCHEMA.md`
- `orchestrator/universal_task_lifecycle.py` (new)
- `tests/test_universal_task_lifecycle.py` (new)
- `tests/test_self_maintenance_profile.py`
- `tests/test_control_loop.py`

The next code task MUST NOT modify `orchestrator/projects.json`, `orchestrator/control_loop_service.py`, either publisher gate, project registry, operation profiles, runtime state, queues, systemd, secrets, production, or databases. Publisher routing and any capability change belong to later, separately owner-approved slices. This ordering makes the lifecycle and its denial semantics testable without weakening the current security boundary or granting AI PROF authority it does not have.
