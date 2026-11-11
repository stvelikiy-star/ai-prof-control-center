# AI PROF CENTER — three confirmed status reconciliation fixes

This is a targeted correction to the existing owner-only Gateway, FIFO runtime and Telegram modules; no new queue, worker or services.

1. FIFO terminal ledger is checked before attempting to re-stage the same Issue. A completed/blocked legacy record follows the existing `report_existing` path, validates the unchanged owner contract, verifies independent remote completion evidence for PASS, posts at most one authorized result comment, and updates the local Gateway state. Never creates a second executable ticket.
2. The running V4 Gateway poll now reconciles terminal legacy records whose owner Issues were closed before results were saved. It retrieves only the original GitHub Issue metadata, verifies identity, exact body/title/profile and pinned SHA against the unique local terminal record, and updates only the local status. It does not reopen closed Issues, post comments or retry Codex. Closed result stays marked `result_reported=False` to avoid falsely claiming delivery. The original open-only authorization policy remains unchanged for new jobs.
3. Owner Telegram `/ai status` includes an independent metadata-only FIFO line, or an explicit `FIFO: не активирована`/`FIFO: недоступна`. Existing autonomous counts are shown separately to avoid counting the same ticket twice; no task body, command, secret or identifier is included.

## Verification

- Source issue #212 closed with one administrative comment, local blocked ledger exists and Gateway state was queued; this discrepancy motivated the fix.
- Synthetic completed and blocked FIFO-to-Gateway callback tests, repeated polling, closed Issue identity/digest mismatch, offline GitHub and unsafe FIFO directory cases pass.
- Full regression on isolated branch: 863/863 PASS. No actual Codex tasks, no customer project edits, and no FIFO route switch.

## Rollout restriction

- Until both the existing systemd Gateway and Telegram Bridge services load the exact installed code, the fix is not fully live.
- Because route attestation pins the Gateway source digest, a code update causes `gateway_attested=false` until a controlled systemd restart, while the current route remains `legacy`.
- Do not turn on FIFO until live service attestation is verified and one controlled owner-approved test completes Gateway -> FIFO -> bounded worker -> remote PR evidence -> Telegram.
