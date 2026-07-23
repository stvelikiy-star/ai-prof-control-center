# AI PROF Orchestrator Stage 01A Test Evidence

Execution environment:
- OS: Ubuntu 26.04 LTS
- Kernel: Linux 7.0.0-28-generic x86_64 GNU/Linux
- Python: Python 3.14.4
- User: agent
- Host: agent-VivoBook-ASUSLaptop-X412UA
- Working directory: /home/agent/projects/ai-prof-control-center
- TMPDIR: /home/agent/tmp/ai-prof-tests
- Test runner: Python unittest discovery
- Test package marker: tests/__init__.py
- Discovery pattern: test_*.py
- Exact command: TMPDIR=/home/agent/tmp/ai-prof-tests PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p "test_*.py" -v
- Executed at UTC: 2026-07-23T22:06:48Z

## Test output

test_atomic_move_rejects_existing_destination_without_overwrite (test_orchestrator.OrchestratorSecurityTests.test_atomic_move_rejects_existing_destination_without_overwrite) ... ok
test_atomic_move_success (test_orchestrator.OrchestratorSecurityTests.test_atomic_move_success) ... ok
test_context_contents_loaded (test_orchestrator.OrchestratorSecurityTests.test_context_contents_loaded) ... ok
test_context_escape_and_missing_file_rejected (test_orchestrator.OrchestratorSecurityTests.test_context_escape_and_missing_file_rejected) ... ok
test_dirty_git_repository_detected_without_head_change (test_orchestrator.OrchestratorSecurityTests.test_dirty_git_repository_detected_without_head_change) ... ok
test_dirty_project_cycle_moves_to_blocked (test_orchestrator.OrchestratorSecurityTests.test_dirty_project_cycle_moves_to_blocked) ... ORCHESTRATOR_STOPPED: BLOCKED_DIRTY_PROJECT: /home/agent/tmp/ai-prof-tests/tmpgxsyhfcw/project
ok
test_full_successful_process_one_cycle_preserves_target_project (test_orchestrator.OrchestratorSecurityTests.test_full_successful_process_one_cycle_preserves_target_project) ... ok
test_lock_contention_real_function (test_orchestrator.OrchestratorSecurityTests.test_lock_contention_real_function) ... ok
test_missing_command_and_environment_block (test_orchestrator.OrchestratorSecurityTests.test_missing_command_and_environment_block) ... ok
test_process_one_stops_safely_when_atomic_move_unavailable (test_orchestrator.OrchestratorSecurityTests.test_process_one_stops_safely_when_atomic_move_unavailable) ... BLOCKED_ATOMIC_NOREPLACE_UNAVAILABLE
ok
test_redaction_variants (test_orchestrator.OrchestratorSecurityTests.test_redaction_variants) ... ok
test_renameat2_unavailable_blocks_with_no_fallback (test_orchestrator.OrchestratorSecurityTests.test_renameat2_unavailable_blocks_with_no_fallback) ... ok
test_task_parser_required_fields (test_orchestrator.OrchestratorSecurityTests.test_task_parser_required_fields) ... ok
test_work_branch_validator_real_function (test_orchestrator.OrchestratorSecurityTests.test_work_branch_validator_real_function) ... ok

----------------------------------------------------------------------
Ran 14 tests in 0.187s

OK
STAGE_01A_VALIDATION_PASS

## AK BERMET integrity

- HEAD before: cb65480e1106993bc119b92ab7dc1c572a9223a9
- HEAD after: cb65480e1106993bc119b92ab7dc1c572a9223a9
- Branch before: develop
- Branch after: develop
- Status before: clean
- Status after: clean
