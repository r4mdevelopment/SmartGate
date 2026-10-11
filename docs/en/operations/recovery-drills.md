[SmartGate](../../../README.md) / [Documentation](../README.md) / Recovery drills

[English](recovery-drills.md) · [Русский](../../ru/operations/recovery-drills.md)

# Test recovery separately from your working DNS

A recovery drill checks that a version update retains ownership and that a verified backup can restore state. It does not require breaking a working installation or switching device DNS.

## Isolated environment

Use a new empty disposable DNS profile and a separate test state branch. Check that its profile ID differs from every active device profile. Use independent temporary encryption keys. Record the created profile and branch so cleanup can remove only those resources. A complete Git-state backup also needs its repository, branch, target binding and exact revision; preserve these privately alongside the encrypted file.

## Scenarios

1. Create one test DNS rule and save its exact owned record ID. Save the baseline DNS list and state.
2. Exercise the supported ownership-format upgrade from schema 1 to schema 2. Confirm that record IDs and contents are unchanged, the old path is removed and the new target binding is readable. This checks the state upgrade path; it does not prove compatibility of every future package version.
3. Run key-rotation preview and apply. Verify the backup with the old key and the current document with the new key. DNS records must remain identical.
4. Make an inspected metadata-only change on the test state. Decode the protected backup, validate its target and ownership against the current DNS snapshot, then save the restored document through CAS using the current revision and key. Verify the complete restored document, allowing only the new revision to differ. Do not reset Git history or treat unreadable state as absent.
5. Confirm ordinary no-change synchronization after rotation on integration fixtures. Test pending journals, corrupt ciphertext, conflicts and uncertain writes without repeating mutations. These fault-injection tests run offline; they are not destructive live experiments.
6. Remove only the test rule/profile and test state branch. Read back the working profile and state to confirm they were not changed.

Run the automated fixture scenarios with:

```bash
python -m pytest tests/integration/test_state_key_rotation.py tests/integration/test_encrypted_lifecycle.py -q
```

The isolated live drill supplements the tests; it does not enable production writes for reference DNS adapters or certify every failure mode. Operational recovery on a real installation still requires inspection and the [runbook](runbook.md).

[Key rotation](key-rotation.md) · [Upgrade guide](upgrading.md)
