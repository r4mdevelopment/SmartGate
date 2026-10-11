[SmartGate](../../../README.md) / [Documentation](../README.md) / Privacy

[English](privacy.md) · [Русский](../../ru/operations/privacy.md)

# State and report privacy

SmartGate stores route settings and a DNS change journal. This is not a history of websites visited by the user: the controller receives neither browser traffic nor device DNS query logs. The journal helps identify completed operations after an interruption, retain record ownership and safely perform recovery or rollback.

## What is protected

| Data | Storage |
| --- | --- |
| Ownership, routes, source snapshots, plan and journal | The entire document is encrypted in live Git storage; the default branch is `smartgate-state` |
| Detailed report: domains, IPs, checks and errors | Encrypted `report.enc.json` artifact |
| Summary, stdout, `report.json`, `report.md` in Actions | Status, aggregates and fixed error categories |
| Pre-rotation backup | `before.enc.json` in the rotation artifact, encrypted with the old key |
| Configuration, service catalog and manifest, file paths, ciphertext sizes, envelope format, commit times and authorship | Visible to repository readers |
| Local file state and ordinary local reports | Unencrypted; keep outside Git and restrict access |

AES-256-GCM uses a fresh random nonce for each save and checks integrity on read. State is bound to the repository, branch and target instance; reports are bound to the repository, Actions run ID and attempt. Tampering, a different key or incorrect binding blocks reads. Unreadable state must not be treated as missing state to claim DNS records again.

## GitHub service logs

Store the DNS profile ID, namespace and API/DoH URLs in Environment **Secrets** with the same names, rather than Variables. The workflow prefers Secrets, and GitHub masks them in its environment output before Python starts. The controller's private projection cannot remove data already printed by GitHub. Old Variables are supported for transition and may expose their values in logs. Simply creating an unused Secret is not enough: the workflow must read it.

## Prepare a key

Add the Environment Secret **`SMARTGATE_STATE_ENCRYPTION_KEY`**: 32 random bytes encoded in standard Base64. It is independent of the DNS API key. Generate the key on a trusted device and immediately save it in a secure secrets manager; transfer the value directly into the GitHub Secret and an independent secure backup.

Do not print the key in a terminal or pass it through a workflow, commit, Issue or public chat. The generator must produce exactly 32 cryptographically random bytes encoded in Base64, rather than an ordinary 32-character password. The controller itself continues to run in Actions.

Each installation and each fork gets its own key. Keep an independent backup in a password manager or another secure store. A lost key cannot be recovered from a GitHub Secret, ciphertext or the DNS API. Deleting state and generating a new key does not restore ownership of existing records. Manual [key rotation](key-rotation.md) is supported; automatic keyrings are not.

## New installation

Fork with **Copy the main branch only**; do not copy someone else's state branch. Add your own key and DNS credentials. The first explicitly authorized bootstrap creates encrypted state for an empty DNS target. Someone else's ciphertext is rejected even if the profile name matches.

After renaming or transferring the repository, the previous ciphertext binding will no longer match. Before such an operation, stop writers and prepare a separate verified migration; there is no automatic rebinding.

## Migrating an existing installation

1. Keep `SMARTGATE_APPLY_ENABLED=false` and wait for writers to finish. Save protected copies of state and the DNS record list.
2. The old state must already use schema 2 and be bound precisely to the DNS target. For schema 1, first complete `state-upgrade` with the previous controller version. Recover a pending journal first; closed `verified` or `rolled_back` journals, or no journal, are allowed.
3. Prepare the Secret and key backup, then update the code. Ordinary commands in the new version reject unencrypted Git state.
4. On the default branch, run **`state-encrypt`, `apply=false`, `bootstrap=false`**. Preview reads the old state and verifies ownership through the DNS API.
5. After review, authorize **`state-encrypt`, `apply=true`**. The command saves the same document in encrypted form through CAS and verifies the exact new commit. DNS records do not change. If the outcome is uncertain, determine the current head first; do not repeat the write blindly.
6. Run protected `sync` with `apply=true`, `bootstrap=false`, `require_no_change=true`; a nonzero plan and pending journal are blocked before writes. With a zero-operation plan, there are no DNS operations, but the controller may save updated route metadata, sources and probe cursors. Restoring the normal schedule is a separate decision after acceptance checks.

## Reading a detailed report

Download the artifact for the relevant run, set the key in the local environment variable `SMARTGATE_STATE_ENCRYPTION_KEY`, and run:

```text
python -m smartgate report-decrypt --input report.enc.json --output private-report.json --repository OWNER/REPO --run-id RUN_ID --run-attempt ATTEMPT
```

The command does not access DNS or GitHub, checks the binding and does not overwrite an existing file. Contents are written only to the explicitly specified file. Keep decrypted JSON outside the repository; do not attach it to public Issues without removing private data yourself.

Local live commands using Git state also use private reports. Alongside credentials and the key, set `GITHUB_REPOSITORY`, `GITHUB_RUN_ID` and `GITHUB_RUN_ATTEMPT` to bind the artifact. Actions sets them automatically. Offline fixtures and `validate` do not require a key.

## Before making the repository public

Encryption protects new writes. Old commits containing plaintext state, previous Actions logs, Summary and artifacts may remain accessible according to their permissions and retention periods. Git objects may persist and remain accessible by a known SHA after branch deletion or history rewriting. Simply deleting a file or branch is not enough to prove that old contents were removed. Before going public, separately review history, old runs, artifacts and all copies. Clean history only after creating a protected backup and verifying recovery.

Encryption does not hide public source code, configuration, the service catalog and manifest, report aggregates, run times, ciphertext sizes or file names. The envelope contains visible format/version/algorithm and nonce; AAD binding comes from the trusted context at read time, rather than being extracted from ciphertext. A trusted workflow editor or compromised runner with access to the Secret can read the data. AES-GCM does not prevent an administrator from reverting to old valid ciphertext; CAS, DNS binding and API read-back checks remain necessary.

[Actions setup](actions.md) · [State and journal](state.md) · [Troubleshooting](runbook.md)
