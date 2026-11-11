# AI PROF Control Plane: encrypted backup and safe recovery

Status verified on Ubuntu at 2026-11-09, Asia/Bishkek.

## Coverage and schedule

- Primary n8n database, volume, config backup: system timer 03:00 local, `/home/agent/ai-prof-backups/YYYY-MM-DD_HHMMSS/`.
- Additional AI PROF control-plane state: user timer 03:40 local, `/home/agent/ai-prof-backups/control-plane-encrypted/control-plane-*.tar.gz.gpg`.
- New state backup includes task queues, Stage 01B/01C logs, campaign/plans/evidence/reports, GitHub gateway state, Telegram delivery checkpoints and update offset. Excludes transient locks/PIDs and the large rolling orchestrator loop log.
- Encryption: GPG AES-256 symmetric; local recovery passphrase at `/home/agent/.config/ai-prof-control-center/recovery-key.gpg-passphrase` (0600); archives and portable SHA-256 manifests (0600).
- Recovery readiness: after every snapshot, decrypt and extract to an isolated `/dev/shm` directory, validate queue count and mandatory checkpoint files, clean the temp directory. No mutation to active queues.
- Installed user timer requires agent lingering; unit templates tracked under `ops/systemd/user/`.

## Safe validation

```bash
systemctl --user status ai-prof-control-plane-backup.timer
systemctl --user show ai-prof-control-plane-backup.service -p Result -p ExecMainStatus
python3 orchestrator/control_plane_backup_health.py
```

Restore is **never** directly into the live state directory. Use a separate empty directory on a controlled host, verify SHA256 and decrypt only after validating that host. Do not print or upload the passphrase in plaintext.

Example on an offline recovery host, with both encrypted archive and key already transferred securely:

```bash
mkdir -m 700 recovery-check
sha256sum -c control-plane-YYYYMMDDTHHMMSSZ.tar.gz.gpg.sha256
gpg --batch --pinentry-mode loopback --passphrase-file /secure/location/recovery-key.gpg-passphrase \
  --decrypt control-plane-YYYYMMDDTHHMMSSZ.tar.gz.gpg | tar -tzf - | head
```

## Recovery limitations and actions

1. **OFF-HOST NOT VERIFIED**: archive and key currently reside on the *same* Ubuntu host. This protects against application-level accidental deletion, not disk loss or theft. Copy the **encrypted archive** to one trusted independent store and the **decryption key** to a separate secure recovery location. Verify retrieving both and decrypting on a different host; do not store the key alongside the archive.
2. The state snapshot and n8n dump are taken at different times (03:40 / 03:00). For disaster recovery verify that the restored service versions and task queues refer to the same expected deployment.
3. The script does not automatically restore or reactivate failed/pending tasks. Restore is a supervised action after reconciling queue state with GitHub and Telegram to prevent double execution or duplicate publishing.
4. The backup does not replace version-controlled source. Git remote for this AI PROF branch must be preserved separately; all changes currently committed locally and **not pushed**.
5. The current Telegram bridge still needs a **root-authorized** service restart to activate V6. Do not run a duplicate Telegram long-poll consumer.
