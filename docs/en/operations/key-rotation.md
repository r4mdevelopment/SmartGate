[SmartGate](../../../README.md) / [Documentation](../README.md) / Key rotation

[English](key-rotation.md) · [Русский](../../ru/operations/key-rotation.md)

# Change the state encryption key

`state-rotate` re-encrypts the existing Git state without changing DNS records. It does not update GitHub Secrets or re-encrypt old commits and reports. Keep the old key to read historical data and the backup.

## Before starting

Use a controller version with `state-rotate`. Save both keys in a secure password manager. The state must use schema 2, match the configured DNS target and contain no pending journal. A `verified` or `rolled_back` journal, or no journal, is allowed. Rotation supports one state document per state branch; extra state files block it.

Set `SMARTGATE_APPLY_ENABLED=false` and wait for any running sync to finish. Do not dispatch another writer until rotation and the Secret switch are complete. The workflow uses the same concurrency group as sync. This maintenance operation leaves device DNS settings and existing DNS records unchanged.

## Rotate through GitHub Actions

1. Keep `SMARTGATE_STATE_ENCRYPTION_KEY` unchanged. Add the temporary Environment Secret `SMARTGATE_STATE_NEW_ENCRYPTION_KEY` with a different key: 32 random bytes encoded in standard Base64. Do not enter keys into workflow inputs.
2. On the default branch, run **SmartGate sync**, `command=state-rotate`, `apply=false`; leave other inputs at their defaults. Preview checks the old state, new-key round-trip, closed journal, target and DNS ownership. It creates no backup and makes no writes.
3. After a successful preview, run **`command=state-rotate`, `apply=true`**. It writes an exclusive local encrypted backup, verifies it, updates the state through non-force CAS and checks the exact commit with the new key. A failed or uncertain reference update is never retried automatically.
4. After success, download the run artifact and retain `before.enc.json` in a secure independent backup. The backup uses the old key. Artifacts expire after seven days; Git history alone is not an independent backup.
5. Replace `SMARTGATE_STATE_ENCRYPTION_KEY` with the saved new key. This is a manual GitHub Secret update. Remove the temporary Secret and run `command=doctor`, `apply=false` to check normal reading and DNS. Detailed rotation reports still use the old key; later reports use the new key.
6. Resume automatic apply only after the normal read-only check succeeds. Restore your previous `SMARTGATE_APPLY_ENABLED` setting.

Between state rotation and the Secret switch, ordinary commands using the old key fail closed. A scheduled doctor can also fail during this maintenance interval; do not bootstrap or delete state to bypass it. Ordinary commands never try the temporary new key.

## If the result is uncertain

Keep automatic apply paused and preserve both keys and the encrypted backup. Inspect the current state branch and exact document using the appropriate key; determine whether the old or new ciphertext is current before changing the primary Secret. Do not repeat `apply` merely because the response was lost. A confirmed write followed by failed DNS read-back also requires inspection, even though rotation itself sends no DNS writes.

To return to an old key after a successful switch, use the same checked rotation process with that saved key as the temporary new key. This creates a new commit; do not reset a Git ref. Restore a backup only after validating repository/branch/target binding, ownership, journal and current revision. [Recovery drills](recovery-drills.md).

A lost old key prevents reading old reports and backups. Rotation is maintenance, not secure erasure of historical data, and does not change the DNS API key.

[Privacy](privacy.md) · [State](state.md) · [Incident response](runbook.md)
