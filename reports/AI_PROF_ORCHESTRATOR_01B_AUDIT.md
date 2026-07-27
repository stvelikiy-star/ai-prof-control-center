# AI PROF Orchestrator Stage 01B Security Audit

Status: implementation complete for independent Codex review. This report does not declare final Stage 01B PASS.

## Bubblewrap boundary

Claude is launched only through the fixed `/usr/bin/bwrap` executable. The runner verifies that Bubblewrap and the resolved Claude executable are regular executable files before launch. The sandbox uses `--unshare-all`, restores only the network namespace with `--share-net` for Claude API access, and enables `--die-with-parent`, `--new-session`, and `--clearenv`. Claude's restricted tool policy is validated before the Bubblewrap process is spawned.

The sandbox starts from a tmpfs root. Its runtime mounts are:

- read-only `/usr`, `/bin`, `/sbin`, `/lib`, `/lib64`, and `/lib32` when present;
- selected read-only resolver, host-name, CA, and passwd files under `/etc`;
- the resolved Claude executable at `/run/ai-prof/claude` when it is outside the runtime directories;
- the isolated scratch directory, read-only, at `/run/ai-prof/scratch`;
- only the isolated staging workspace, read-write, at `/workspace`;
- optional Claude credentials only: host `~/.claude` and `~/.claude.json` are mounted read-only at `/home/claude/.claude` and `/home/claude/.claude.json`;
- private tmpfs `/tmp`, plus isolated `/proc` and `/dev`.

`HOME` is `/home/claude`, `PATH` is fixed, and `--chdir` is `/workspace`. The target project, Control Center root, full host home, and host `/tmp` are not mounted. The target project is never used as the Claude process working directory and is never directly writable by Claude.

Before staging, the runner also rejects a target project that is inside, contains, or otherwise overlaps any runtime or Claude-credential mount. This prevents an unusual project location such as `/usr/...` or `~/.claude/...` from becoming indirectly readable through a required read-only mount.

## Isolated staging lifecycle

After task, project, environment, Git cleanliness, branch, context, executable, and raw scope validation, the runner creates a temporary isolation root with separate `workspace` and `scratch` directories. Only existing approved `Scope-Files` are copied into the workspace. Approved new files begin absent. Copying rejects symlinks and non-regular/non-directory objects.

A baseline hash of approved target files is taken before staging. Claude receives the task and the five fixed context files through stdin; context files are not placed in the workspace. After a successful Claude exit, the runner walks the complete workspace, rejects unexpected paths, symlinks, and special files, computes a content diff, validates every changed path against `Scope-Files`, and materializes an in-memory change set. The temporary workspace is destroyed before the target branch is prepared or files are applied.

Failed or blocked runs do not apply staged files. A successful run applies only validated changed files; context-only and other project files are never copied back.

## Path, symlink, and TOCTOU protections

Every raw `Scope-Files` value is checked before normalization. Empty components, surrounding whitespace, NUL bytes, POSIX absolute paths, Windows drive paths, UNC/backslash paths, `.` components, and `..` traversal are rejected. The project-relative path is then walked with `lstat`; parent and final-component symlinks are rejected, including symlinks whose targets remain inside the project. Scope entries and workspace output must be regular files or directories.

Immediately before application, approved source hashes are checked for concurrent changes. Every destination is preflighted before the batch and revalidated again immediately before its operation. Existing parent and leaf symlinks and special files are rejected. Writes use same-directory temporary files and `os.replace`, so a planted leaf symlink is replaced rather than followed.

## Atomic application

Application is all-or-nothing at the approved-file batch level:

1. Validate every change against the approved scope and every destination path before writes.
2. Capture the original bytes or absence of every destination.
3. Revalidate each destination immediately before mutation.
4. Write each file through a same-directory temporary file plus `os.replace`, or safely delete a regular file.
5. If any operation fails, restore every original file in reverse order, remove newly created files, and remove empty directories created by the batch.
6. If rollback itself cannot complete, route the task to blocked with `BLOCKED_PATCH_ACCESS` and report the incomplete rollback explicitly.

This provides atomic logical outcome under ordinary filesystem failures. It is not a transactional filesystem primitive; abrupt power loss or process termination during the rollback window remains a residual risk.

## AccessFailure routing

All `AccessFailure` subclasses route atomically to `queue/blocked`; ordinary Claude/code/check failures route to `queue/failed`; only successful runs move to `queue/pending_codex`.

Subclasses and status codes:

- `ClaudeCliMissingError` — `BLOCKED_CLI_MISSING`
- `ClaudeNotExecutableError` — `BLOCKED_CLAUDE_NOT_EXECUTABLE`
- `ClaudeAuthError` — `BLOCKED_CLAUDE_AUTH`
- `ClaudePermissionError` — `BLOCKED_CLAUDE_PERMISSION`
- `ProjectAccessError` — `BLOCKED_PROJECT_ACCESS`
- `PermissionAccessError` — `BLOCKED_PERMISSION_DENIED`
- `ContextAccessError` — `BLOCKED_CONTEXT_ACCESS`
- `EnvironmentAccessError` — `BLOCKED_ENVIRONMENT_ACCESS`
- `GitAccessError` — `BLOCKED_GIT_ACCESS`
- `BranchAccessError` — `BLOCKED_BRANCH_ACCESS`
- `PatchAccessError` — `BLOCKED_PATCH_ACCESS`
- `ScopeAccessError` — `BLOCKED_SCOPE_ACCESS`
- `ScopeReadError` — `BLOCKED_SCOPE_READ`
- `WorkspaceWriteError` — `BLOCKED_WORKSPACE_WRITE`
- `TempDirectoryError` — `BLOCKED_TEMP_DIRECTORY`
- `DirtyProjectError` — `BLOCKED_DIRTY_PROJECT`
- `InvalidBranchNameError` — `BLOCKED_INVALID_BRANCH`
- `ConcurrentModificationError` — `BLOCKED_CONCURRENT_MODIFICATION`
- `ClaudePolicyError` — `BLOCKED_CLAUDE_POLICY`
- `SandboxCliMissingError` — `BLOCKED_SANDBOX_MISSING`
- `SandboxNotExecutableError` — `BLOCKED_SANDBOX_NOT_EXECUTABLE`
- `SandboxSetupError` — `BLOCKED_SANDBOX_SETUP`
- `SandboxExposureError` — `BLOCKED_SANDBOX_EXPOSURE`
- `ProcessLaunchError` — `BLOCKED_PROCESS_LAUNCH`
- `InfrastructureTimeoutError` — `BLOCKED_INFRA_TIMEOUT`

Authentication markers and account/entitlement/permission markers are classified separately. Bubblewrap setup diagnostics, subprocess permission errors, relevant OS launch errors, infrastructure timeouts, temporary-directory failures, Git access failures, and apply failures are blocked rather than mislabeled as ordinary task failures.

## Executable tests

The suite includes deterministic fake-Claude process tests that invoke the real `/usr/bin/bwrap` without contacting Claude or consuming credits. They verify an allowed workspace write, denial of host-file reads and outside writes, denial of another project, and failure of workspace symlink redirection. These tests skip only when the host kernel refuses the namespace creation required by Bubblewrap.

Unit/integration coverage also verifies the exact Bubblewrap argv and internal mount paths; no direct target cwd; project immutability before and during Claude; failed and blocked immutability; approved-only application; context exclusion; unexpected workspace files, symlinks, and special files; raw path bypasses; parent/final symlinks; executable and launch failures; timeout/authentication/account/Git/temp/apply routing; batch rollback; and distinct blocked, failed, and pending-Codex outcomes.

No test calls the real Claude service.

## Residual risks

- Bubblewrap requires kernel support for unprivileged user namespaces or an appropriately configured installation. Unsupported hosts fail closed as `BLOCKED_SANDBOX_SETUP`.
- Network access is shared because Claude requires its API. Filesystem isolation and Claude tool restrictions remain the primary controls; this stage does not provide destination-level network filtering.
- Read-only runtime libraries and selected `/etc` files expose ordinary system metadata required for process and TLS operation.
- Optional Claude credential mounts expose Claude-specific local credential state read-only inside the sandbox. Environment authentication is limited to `ANTHROPIC_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`.
- Batch rollback protects against handled application errors, but cannot guarantee recovery after machine failure, filesystem corruption, or an uncatchable process termination.
- A concurrent actor with permission to replace project directories can continue racing filesystem checks. Component `lstat`, immediate destination revalidation, source hashes, and atomic leaf replacement narrow and fail closed on observed races, but Linux `openat2`/directory-fd confinement would further strengthen this boundary.
- Required checks run after application against the real worktree under the fixed local command allowlist. A failing check routes to failed but does not roll back already-applied changes; independent Codex review remains required before any later integration action.

## Stage 01A and release actions

Stage 01A files are unchanged. No commit, merge, push, deployment, or branch creation is part of this implementation or audit. Final Stage 01B approval remains an independent Codex decision.
