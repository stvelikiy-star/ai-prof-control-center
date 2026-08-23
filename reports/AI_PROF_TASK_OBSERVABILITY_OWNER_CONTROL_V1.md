# AI PROF Task Observability and Owner Control Contract V1

Status: implementation-ready contract; no command or runtime implementation is included.

Task: `AI_PROF_CONTROL_CENTER_20260822T181654Z_FF24FA` / authorized private GitHub issue `#103`.

## 1. Purpose and evidence boundary

This contract defines the minimum owner-facing projection and control semantics for autonomous AI PROF work. It covers the logical operations `status`, `tasks`, `task-detail`, `blocked`, `approve`, `start`, `stop`, and `report`. These names describe capabilities, not proven CLI, Telegram, ChatGPT, HTTP, or other commands.

The Stage 01B workspace contained no regular repository files before this report was created. Consequently, no current handler, queue schema, log schema, command syntax, or implementation path could be verified. The validated task envelope and the supplied `SYSTEM_INSTRUCTIONS.md`, `SOURCE_POLICY.md`, `STATE.md`, `APPROVAL_MATRIX.md`, and `DECISIONS.md` excerpts are the only available policy evidence. In particular, they establish that:

- autonomous work is limited to a validated scope;
- missing authority means `OWNER_ACTION_REQUIRED`, never implied permission;
- repository merge and live activation are separate gates;
- production deployment is disabled by default;
- queue mutation, runtime/state access, systemd operations, secrets, production/database access, commits, pushes, merges, and deployment are not authorized by this documentation task;
- Telegram remains a parallel channel and a future ChatGPT gateway must not replace it;
- existing AK BERMET submission/status compatibility must be preserved.

This report therefore makes no claim that an owner-facing operation currently exists. A later implementation must bind this contract to verified repository adapters without broadening authority. If an implementation assumption cannot be proven, the result is `INCONSISTENT` or `OWNER_ACTION_REQUIRED`, not a guessed state or action.

## 2. Security and authority invariants

Every read projection and control request MUST preserve these invariants:

1. The validated task envelope is the authority ceiling. A presentation or adapter cannot enlarge scope, capabilities, operation profile, approval, or target environment.
2. Observation is read-only. Merely viewing status, task details, blocked tasks, or reports cannot renew leases, acknowledge gates, change queue order, close issues, update PRs, or write runtime state.
3. Control is structured and allowlisted. No field accepts arbitrary shell, paths, environment names, credentials, SQL, service operations, or free-form executable content.
4. Identity, authorization, and task/gate binding are checked on every control request. Channel identity alone is insufficient.
5. Approval is explicit, single-purpose, bounded, auditable, expiring, and non-replayable. Urgency, issue labels, issue/PR comments, previous approvals, and a `start` request never imply approval.
6. `start`, `stop`, and `approve` are requests until independently confirmed. Presentation MUST NOT turn accepted intent into a claimed runtime result.
7. Redaction precedes persistence and presentation. Evidence may expose stable references, timestamps, outcome codes, and sanitized summaries, but never secrets, tokens, environment values, private keys, credentials, or raw untrusted payloads.
8. Repository delivery, merge, deployment, production writes, database changes, secrets, and service management remain distinct owner gates. One gate cannot authorize another.
9. Invalid, absent, stale, unauthorized, or contradictory evidence fails closed.

## 3. Canonical record and source roles

The owner view is a derived projection, not a new source of truth. Each projected task record MUST include:

- `task_id`: immutable validated task identifier;
- `title`: sanitized concise title;
- `project_id` and target repository identity, when validated;
- `scope_files` and forbidden actions from the validated envelope;
- `source_issue`: stable repository/issue identity if the task was sourced from GitHub;
- `queue_ref`: opaque stable queue record identifier;
- `run_id` and `attempt`: present only when a run was allocated;
- `worker_ref`: non-sensitive worker/lease identity if active;
- `pr_ref`: stable repository/PR identity if delivery produced a PR;
- `report_ref`: immutable or content-addressed report identity when available;
- `status`: exactly one canonical value from section 4;
- `candidate_status`: the last reconciled lifecycle candidate, shown only when `status` is `STALE` or `INCONSISTENT`;
- `blocked_reason`: structured object from section 8 when applicable;
- `pending_gates`: structured gates from section 9;
- `created_at`, `updated_at`, `last_reconciled_at`, and `freshness_until`, as UTC timestamps;
- `evidence`: source references and observed timestamps sufficient to explain the status;
- `revision`: monotonic projection revision used for idempotency and stale-write rejection.

Source roles are deliberately narrow:

| Source | May establish | Must not establish alone |
|---|---|---|
| Validated task envelope | identity, authorized scope, forbidden actions, required gates/checks | execution, success, merge, deployment |
| Queue/runner state | enqueue order, eligibility, allocation, run/stop intent, lease, attempt, exit outcome | GitHub issue/PR truth, owner approval, production activation |
| GitHub issue | source intent, owner-visible discussion, issue open/closed state | runtime state, queue eligibility, approval merely from prose/labels |
| Pull request | proposed delivery, review/check/merge state | runtime completion, deployment, authority beyond task scope |
| Structured logs | time-bounded runtime activity and sanitized event outcomes | authority, approval, durable completion without terminal evidence |
| Check results | named check execution and outcome for a specific revision | merge, deployment, unrelated gate approval |
| Final report | bounded outcome summary and evidence index | facts unsupported by referenced evidence |
| Deployment/production evidence | activation only when separately authorized and independently verified | authorization to deploy or mutate production |

Evidence references MUST carry source type, stable source identity, observed timestamp, relevant source revision/version, and a sanitized outcome. Raw log text is supporting detail only; structured events take precedence.

## 4. Canonical task status vocabulary

Only the following uppercase values may be shown as `status`. Adapters may map internal names to them but may not add channel-specific lifecycle values.

| Status | Meaning | Minimum evidence required before display |
|---|---|---|
| `QUEUED` | Accepted into the validated queue but not yet eligible to start. | Valid task envelope; matching durable queue record; no allocation or active lease; queue observation within `freshness_until`. |
| `READY` | Eligible for owner/autonomous start under current authority. | All `QUEUED` evidence; prerequisites satisfied; no unresolved gate or block; capacity/policy eligibility evaluated at the observed revision. |
| `START_REQUESTED` | A valid start request was accepted, but execution is not confirmed. | Authorized, idempotent start receipt bound to task/revision plus runner allocation request; no live lease/heartbeat yet. |
| `RUNNING` | An allocated worker is currently executing this attempt. | Matching task/run/attempt; unexpired worker lease; heartbeat or structured progress event within its declared freshness window; no terminal event. A log line alone is insufficient. |
| `BLOCKED` | Work cannot proceed because of a non-owner dependency or remediable execution condition. | Structured current blocked reason, supporting evidence reference, responsible resolver, next action, and recheck/freshness deadline. No runnable worker may be claimed. |
| `OWNER_ACTION_REQUIRED` | Progress requires an explicit owner decision or action. | One or more unresolved structured owner gates or owner-owned blocked reasons; exact requested action and bounded consequences; no inferred approval. |
| `STOP_REQUESTED` | A valid stop request was accepted, but quiescence is not confirmed. | Authorized, idempotent stop receipt bound to task/run/attempt plus delivery/acknowledgement evidence; terminal/quiescent evidence absent. |
| `STOPPED` | The attempt stopped without completing the requested work. | Runner terminal stop event; lease released/expired and no later heartbeat; exit classification `stopped`; preserved logs/report; queue and run records agree. |
| `AWAITING_REVIEW` | Bounded work and required checks completed, but an external review/merge gate remains. | Successful run terminal event; required checks tied to produced revision pass; final report exists; delivery artifact/PR is open when required; unresolved review/merge gate is explicit. |
| `COMPLETED` | The task's authorized deliverable is complete. It does not imply deployment. | Successful terminal run; all required checks pass for the delivered revision; final report; queue terminal outcome; issue and PR state reconcile under section 6; no unresolved gate within task scope. For code delivery requiring merge, the PR must be merged before `COMPLETED`. |
| `FAILED` | The attempt ended unsuccessfully and did not produce a valid completed result. | Terminal runner event and exit classification; lease inactive; sanitized failure summary and evidence; retry eligibility recorded. Failed checks require this state unless a still-running retry attempt is separately active. |
| `CANCELLED` | The task was withdrawn before completion by authorized policy/owner action. | Authorized cancellation receipt, queue terminal cancellation, no active lease, and cancellation reason. Cancellation is distinct from stopping a single attempt. |
| `STALE` | Previously coherent evidence is too old to support its candidate lifecycle status. | A required lease, heartbeat, reconciliation, block recheck, start/stop acknowledgement, or other `freshness_until` has expired and no newer terminal evidence resolves it. |
| `INCONSISTENT` | Required sources contradict each other, identities/revisions do not match, or reconciliation cannot safely choose a lifecycle status. | At least one recorded contradiction, missing required linkage, source read failure that prevents verification, malformed evidence, revision regression, or ambiguous multiple active attempts. |

`STALE` and `INCONSISTENT` are fail-closed presentation states. The view MAY show `candidate_status` for diagnosis, but controls default to denied and the candidate may not be presented as authoritative. `INCONSISTENT` takes precedence over `STALE`; otherwise a terminal status supported by coherent terminal evidence takes precedence over older non-terminal evidence.

State transitions MUST be monotonic within an attempt except for explicit retry allocation, which increments `attempt` and begins at `QUEUED`, `READY`, or `START_REQUESTED`. A late event from an older attempt cannot alter a newer attempt. Terminal states cannot revert without a new validated task revision or retry attempt. `AWAITING_REVIEW` may become `COMPLETED`, `BLOCKED`, `OWNER_ACTION_REQUIRED`, `STALE`, or `INCONSISTENT`, but never `RUNNING` without a new attempt.

## 5. Owner-facing capability semantics

These are logical API semantics. A later channel may choose different user-facing spelling while returning the same structured result.

### `status`

Returns a concise control-center summary: projection reconciliation time and health; counts by canonical status; active tasks; unresolved owner gates; stale/inconsistent count; and the most recent terminal transitions. It MUST distinguish repository delivery, merge, and live activation. Partial source failure makes overall health degraded and every affected task `INCONSISTENT`; it must not silently omit those tasks.

### `tasks`

Returns a stable, paginated task list. Default ordering is: `INCONSISTENT`, `STALE`, `OWNER_ACTION_REQUIRED`, `BLOCKED`, active states, then newest terminal states. Each row contains task ID, title, status, attempt, age in state, freshness, pending-gate count, and one safe next action. Filters are allowlisted canonical statuses/project identifiers only. Pagination uses an opaque revision-bound cursor so concurrent updates cannot silently reorder a page.

### `task-detail`

Returns one exact task identity and revision, scope/forbidden actions, canonical and candidate status, attempt history, structured block/gates, reconciled issue/PR/check/run/report references, freshness, contradictions, sanitized recent structured events, and permitted next actions. It does not return raw untrusted task prose, environment values, raw logs, secrets, or unrestricted paths.

### `blocked`

Returns only `BLOCKED` and `OWNER_ACTION_REQUIRED` tasks, ordered by owner urgency then age. It groups owner gates separately from non-owner dependencies and shows reason code, safe summary, resolver, blocked since, recheck deadline, evidence, and exact next action. Empty means "no blocked tasks proven at this reconciliation revision," not "the system has no problems."

### `approve`

Accepts only a previously presented `gate_id`, `task_id`, expected task `revision`, one allowlisted decision (`approve` or `deny`), and an idempotency key. It MUST re-read the gate, identity, authority, expiry, scope hash, task/run binding, and current revision before recording a decision. Approval is rejected for unknown, changed, expired, already-consumed, stale, or inconsistent gates. Success means only "decision durably recorded"; downstream action still requires its own evidence. A denial keeps or transitions the task to `OWNER_ACTION_REQUIRED`, `BLOCKED`, `CANCELLED`, or `FAILED` according to the gate's declared denial outcome.

### `start`

Accepts `task_id`, expected `revision`, and idempotency key. It is permitted only from fresh `READY`, with no unresolved gates, within the validated scope and operation profile, and after concurrency/prerequisite checks. Acceptance yields `START_REQUESTED`; only a live lease and fresh worker event may yield `RUNNING`. Repeated identical requests return the original receipt. A different request against the same revision conflicts. It cannot authorize commits, pushes, merges, deployment, production access, secrets, or any other separate gate.

### `stop`

Accepts `task_id`, exact active `run_id`/`attempt`, expected revision, a bounded reason code, and idempotency key. It requests cooperative cancellation and yields `STOP_REQUESTED`. It MUST target only the current run and must not execute user-supplied kill commands, destructive cleanup, rollback, queue deletion, or unrelated service actions. `STOPPED` is shown only after confirmed quiescence and lease release. If acknowledgement or quiescence freshness expires, the status becomes `STALE`; contradictory continued/terminal activity becomes `INCONSISTENT`. Terminal tasks return an idempotent no-op result.

### `report`

Returns a redacted, immutable outcome projection for one task/attempt/revision: authorized goal and scope, resulting status, changed-artifact manifest, checks and exact outcomes, owner decisions, issue/PR linkage, timestamps, failure/block summary, and stable evidence references. A report MUST state explicitly what was not performed, especially merge, deployment, production/database mutation, secrets access, and service activation. Missing terminal evidence is shown as incomplete; report existence alone never proves completion.

Every capability response includes `projection_revision`, `last_reconciled_at`, `freshness_until`, `source_health`, and `permitted_next_actions`. A mutating request also returns an opaque receipt, decision (`accepted`, `rejected`, or `conflict`), and reason code. It never returns a success claim for work not independently confirmed.

## 6. Queue, GitHub issue, PR, checks, report, and log reconciliation

Reconciliation is deterministic and read-only:

1. Resolve the immutable task identity and scope hash from the validated envelope. Reject cross-repository, cross-project, cross-task, or mutable-title-only linkage.
2. Read the durable queue/task record and all attempts. Validate monotonic revisions and ensure at most one current active attempt.
3. Read structured runner lease/events and required check results for the exact run, attempt, and delivered revision.
4. Resolve the GitHub issue and PR only through durable identities stored in the task/delivery evidence. Search results, branch-name similarity, prose, or labels are insufficient linkage.
5. Resolve the final report and verify that its task/run/revision/check references match.
6. Evaluate freshness independently per source, then apply the status evidence table and precedence rules.
7. Store/present the projection revision and a reasoned reconciliation result. Reconciliation itself must not mutate queue, GitHub, PR, runtime, or report state.

Required agreement rules:

- A queue record without a matching validated envelope is `INCONSISTENT` and cannot start.
- A validated task with a source issue whose identity cannot be verified is `INCONSISTENT` if issue state is required for the claimed status. Temporary source unavailability cannot be treated as agreement.
- Issue closure alone never yields `COMPLETED`, `CANCELLED`, or approval. An issue closed before supported terminal delivery is `INCONSISTENT` unless a verified authorized cancellation links the closure.
- An open issue does not invalidate a proven successful run, but prevents `COMPLETED` when closure is part of the task completion policy; show `AWAITING_REVIEW` or `OWNER_ACTION_REQUIRED` as appropriate.
- PR creation alone never proves a successful run or passing checks. A PR linked to the wrong task, repository, head revision, or attempt is `INCONSISTENT`.
- An open, coherent PR with a successful run/report/checks and an explicit review/merge gate yields `AWAITING_REVIEW`.
- A merged PR cannot yield `COMPLETED` unless the delivered revision, required checks, run terminal outcome, report, queue outcome, and issue completion policy agree. A merged mismatched or failing revision is `INCONSISTENT`.
- A closed-unmerged PR is `BLOCKED`, `FAILED`, or `CANCELLED` only when structured evidence explains which; otherwise it is `INCONSISTENT`.
- Required checks are matched by exact check identity and delivered revision. Missing, pending, stale, skipped-without-policy, or revision-mismatched checks are not passing.
- Logs may corroborate `RUNNING`, failure, or stop, but unstructured log text cannot override queue/lease/terminal events. Events received out of attempt or sequence order are retained for audit and excluded from state derivation.
- Queue "running" with no fresh matching lease/event is `STALE`. A live worker for a terminal queue record is `INCONSISTENT`.
- Queue success with failing/missing checks or report is `INCONSISTENT`, not `COMPLETED`. A successful report with a non-terminal run is likewise `INCONSISTENT`.
- Merge and deployment are independent. A merged PR without separately authorized, verified deployment remains "not deployed". Deployment evidence can never repair an unauthorized or inconsistent task state.

When multiple PRs or runs exist, only the explicitly linked current delivery/run participates in the candidate status. Superseded items remain visible in history. Two simultaneously live attempts, two current deliveries, revision rollback, or identity ambiguity yields `INCONSISTENT`.

## 7. Freshness and stale-state detection

Every evidence producer or adapter MUST provide `observed_at` and a policy-derived `freshness_until`; active leases also provide `lease_expires_at`. V1 does not invent wall-clock durations because no repository freshness policy was available in this checkout. A later code task must bind durations in an already owner-approved policy/configuration surface; changing those policy files requires its own scope and approval. Until such a binding is verified, a state that depends on time-varying evidence cannot be claimed fresh.

A task becomes `STALE` when its evidence was coherent but any evidence required for the candidate state has passed `freshness_until`, including:

- `START_REQUESTED` without allocation acknowledgement by its deadline;
- `RUNNING` with an expired lease, heartbeat, or required progress freshness window and no terminal event;
- `STOP_REQUESTED` without stop acknowledgement or quiescence by its deadline;
- `BLOCKED`/`OWNER_ACTION_REQUIRED` past its required recheck time without revalidation;
- an issue, PR, check, queue, report, or log source whose last successful reconciliation expired and is material to the candidate state;
- a projection older than one of the source revisions it claims to represent.

Clock regression, impossible timestamp order, missing required freshness metadata, revision regression, source identity change, or inability to determine whether evidence is merely old versus contradictory yields `INCONSISTENT`, not `STALE`.

Recovery from `STALE` requires a successful full reconciliation with fresh evidence. Recovery from `INCONSISTENT` requires that all recorded contradictions be resolved or explicitly superseded by a higher monotonic revision; simply retrying a source read does not erase the audit record.

## 8. Blocked-reason contract

`BLOCKED` and `OWNER_ACTION_REQUIRED` require a structured `blocked_reason`; free-form prose alone is invalid. The object contains:

- stable `reason_code` from an allowlist;
- sanitized one-sentence `summary` describing the observed condition, not speculation;
- `owner_required` boolean;
- `resolver` (`worker`, `owner`, `external-system`, or a validated project role);
- exact `next_action` in non-executable language;
- `blocked_since`, `last_verified_at`, and `recheck_at`/`freshness_until`;
- supporting evidence references and affected prerequisite/gate IDs;
- retryability and bounded retry policy, if applicable;
- safe impact statement;
- for owner action, the `gate_id`; for no gate, an explicit explanation why no approval can resolve it.

Minimum reason-code families are `MISSING_AUTHORITY`, `OWNER_DECISION`, `SCOPE_MISMATCH`, `PREREQUISITE`, `REQUIRED_CHECK`, `REVIEW_OR_MERGE`, `SOURCE_UNAVAILABLE`, `EXTERNAL_DEPENDENCY`, `RESOURCE_CAPACITY`, and `SECURITY_INVARIANT`. `SOURCE_UNAVAILABLE` becomes `INCONSISTENT` rather than `BLOCKED` whenever the missing source prevents status verification. Secrets or sensitive content may never be embedded in a reason.

A reason lacking its evidence, resolver, next action, or freshness metadata is invalid and forces `INCONSISTENT`. Repeated identical blocks update verification metadata rather than spamming history; a materially changed reason creates a new reason revision.

## 9. Owner-gate presentation and decisions

Every owner gate is shown as a decision card with:

- stable `gate_id`, task ID, gate type, task projection revision, and scope hash;
- current canonical status and freshness;
- one-sentence reason and the evidence that created the gate;
- exact bounded action requested and what it would permit;
- explicit non-authorizations and security boundaries that remain;
- expected effect of `approve` and `deny`;
- creation/expiry time, requesting component, and whether the decision is reversible;
- allowed decisions and a confirmation fingerprint suitable for a compact channel;
- current source/reconciliation health.

The owner must be able to inspect `task-detail` and `report` evidence before deciding. Sensitive values and raw commands are never shown or accepted. High-impact gates such as merge, deployment, production/database mutation, secrets, systemd, migrations, or destructive work cannot be represented as a generic approval; each requires a separately authorized gate type and an outer workflow that is outside this contract.

Gate decisions are append-only audit events. A gate expires on time, task revision change, scope-hash change, run/attempt change where bound, material evidence change, or consumption. Approval of one gate never bulk-approves other tasks or future attempts. A UI must present `approve` and `deny` with equal clarity and must not preselect approval.

## 10. Concise notification policy

Notifications are supplemental; queryable reconciled state remains authoritative. Send one concise notification for:

- first transition to `RUNNING`;
- entry to `BLOCKED` or `OWNER_ACTION_REQUIRED` and any material reason/gate revision;
- accepted/rejected/expired owner decision;
- entry to `STOP_REQUESTED` and its resolution;
- first detection and recovery of `STALE` or `INCONSISTENT`;
- terminal transition to `STOPPED`, `FAILED`, `CANCELLED`, `AWAITING_REVIEW`, or `COMPLETED`.

Do not notify for heartbeats, unchanged polling, duplicate source events, repeated identical blocks, routine queue position changes, or every log line. Deduplicate by task ID + attempt + canonical transition/reason revision + projection revision. Retry delivery with bounded backoff, but notification delivery failure must not change task status or trigger the underlying action.

Each notification contains task ID/title, new status, one-line reason/outcome, freshness time, and one safe next action or stable detail reference. It must distinguish "requested" from "confirmed" and "merged" from "deployed". Telegram, future ChatGPT surfaces, and any other channel show equivalent facts; no channel gains additional authority.

## 11. Fail-closed decision table

| Condition | Projection | Controls |
|---|---|---|
| Required evidence is fresh and agrees | Derived lifecycle status | Only allowlisted actions valid for that status |
| Evidence agrees but required freshness expired | `STALE` with candidate status | Deny `start`/`approve`; allow a validated stop request only if the exact active run can still be safely resolved; otherwise owner action |
| Sources disagree or linkage/revision is ambiguous | `INCONSISTENT` | Deny `start` and `approve`; stop only an exact, independently verified active run; never infer completion |
| Source unavailable and not material to candidate status | Candidate status plus degraded source health | Do not allow an action that depends on the source; notify only on health transition |
| Source unavailable and material to candidate status | `INCONSISTENT` (or `STALE` only when previously coherent evidence simply expired) | Deny dependent controls |
| Approval/authority absent, expired, or mismatched | `OWNER_ACTION_REQUIRED` or `INCONSISTENT` if records contradict | Reject action with stable reason code |
| Unknown internal status/value | `INCONSISTENT` | Deny all mutations except exact safe stop handling |
| Presentation cannot redact/serialize safely | No sensitive payload; generic error with correlation reference | No mutation; preserve sanitized audit evidence |

The system must never select the most optimistic source, silently drop a disagreeing source, infer owner intent, or convert an internal exception into a successful/empty response.

## 12. Later bounded implementation plan

No implementation file existed in the supplied workspace, so the following are exact proposed **new paths**, not claims about current repository layout. Before implementation, a separately authorized discovery/audit must confirm that these paths fit the checked-out repository and identify existing queue/GitHub/PR/log adapters. If they conflict with verified repository structure, issue #103 must be amended with exact existing paths; implementers must not guess or broaden scope.

A minimal later task should scope only:

1. `orchestrator/task_observability.py` — add pure canonical types, evidence validation, freshness evaluation, deterministic reconciliation, precedence, redaction-ready projections, and read-only capability result models. It must not perform queue/GitHub/runtime writes.
2. `orchestrator/owner_control.py` — add structured allowlisted request validation and receipts for `approve`, `start`, and `stop`, delegating to verified existing authority/queue adapters. No shell execution or direct production/service/database/secrets access.
3. `tests/test_task_observability.py` — table-driven status evidence, attempt/revision monotonicity, freshness, source-role, reconciliation, redaction, pagination, and fail-closed tests.
4. `tests/test_owner_control.py` — identity/authorization, gate binding/expiry/consumption, idempotency, stale-revision rejection, start/stop request-versus-confirmation, structured input allowlists, cross-task replay, and security-boundary tests.

The later task must additionally name, after repository discovery, the exact existing adapter and channel files needed to expose these capabilities. Those files are intentionally not invented here. Modifying project registry, global config, task intake, operation profiles, production release policy, sandbox/Codex audit runners, control loop, secrets, live state, systemd, or production/database code remains excluded and requires separate owner-reviewed scope.

Minimum exact test cases for the proposed test files are:

- `test_each_status_requires_all_evidence`
- `test_log_line_alone_never_establishes_running`
- `test_expired_heartbeat_projects_stale`
- `test_unknown_or_conflicting_source_projects_inconsistent`
- `test_issue_closure_alone_never_completes_task`
- `test_pr_revision_must_match_report_checks_and_run`
- `test_merged_is_not_deployed`
- `test_multiple_live_attempts_fail_closed`
- `test_block_reason_requires_owner_next_action_evidence_and_freshness`
- `test_projection_redacts_sensitive_and_untrusted_fields`
- `test_approve_is_bound_expiring_single_use_and_non_replayable`
- `test_start_acceptance_yields_start_requested_not_running`
- `test_stop_acceptance_yields_stop_requested_until_quiescent`
- `test_stale_revision_and_scope_hash_are_rejected`
- `test_idempotency_returns_original_receipt_and_conflicts_on_change`
- `test_read_operations_never_mutate_sources`
- `test_source_failure_is_not_returned_as_empty_success`
- `test_telegram_and_chatgpt_projections_have_equivalent_authority`

Acceptance for that later task requires unit tests for every row of sections 4, 6, and 11; adapter contract tests using sanitized fixtures; proof that observation performs no writes; existing regression tests including AK BERMET submission/status compatibility; the repository's full allowlisted test suite; and a separate Stage 01C security/contract audit. Activation, merge, deployment, service changes, and state/queue mutation remain outer owner-gated operations.

## 13. V1 acceptance checklist

An implementation conforms only if:

- every displayed status has the evidence required in section 4;
- all sources are linked by immutable identity, run/attempt, and revision rather than prose;
- stale and contradictory evidence are visible and deny unsafe controls;
- block reasons and owner gates are structured, current, bounded, and actionable;
- accepted requests are never presented as confirmed outcomes;
- issue, PR, queue, checks, report, logs, merge, and deployment retain their separate meanings;
- notifications are transition-based, deduplicated, redacted, and non-authoritative;
- missing authority always fails closed;
- no observation or presentation path mutates external or runtime state;
- existing security boundaries and owner gates remain unchanged.
