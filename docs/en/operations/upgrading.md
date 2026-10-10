[SmartGate](../../../README.md) / [Documentation](../README.md) / Upgrading

[English](upgrading.md) · [Русский](../../ru/operations/upgrading.md)

# Upgrade a configured fork

A fork contains both upstream code and your configuration. Updating code must preserve your DNS destination, enabled services, secrets, encryption key and `smartgate-state` branch.

## Before updating

1. Read the target version's [changelog](../../../CHANGELOG.md) and release notes. Use an upstream tag or a reviewed commit; a green badge alone does not establish compatibility with your settings.
2. In your fork's `smartgate` environment, set `SMARTGATE_APPLY_ENABLED=false` temporarily. Scheduled runs may still perform read-only checks.
3. Wait for any running sync or recovery to finish. Disabling future apply does not stop an already running command. If a journal is pending, resolve it using the current version before upgrading.
4. Keep a private backup of your TOML configuration, service catalog, current encrypted state file and encryption key. Record the current code commit. Do not copy secrets or decrypted state into Git.

Do not delete the state branch, create a new ledger for an existing profile, or run bootstrap again. The encrypted backup remains bound to its original repository and destination; copying it into a different fork is not a migration procedure.

## Update through GitHub

1. Open a Pull Request comparing your fork's `main` with the selected upstream version using **Compare across forks**. Review the code, workflow and configuration changes before merging.
2. Preserve your values in `config/smartgate.actions.toml`, `config/services.toml` and any relay manifest. Resolve configuration conflicts by reviewing both versions. Do not use **Discard commits** to erase your customizations.
3. Require successful CI and review any migration instructions before merging into your fork's default branch. Never merge upstream changes into `smartgate-state`.
4. Keep the environment Secrets and Variables unchanged unless the release explicitly requires a reviewed migration. The encryption key must stay the same for existing state.

If GitHub's **Sync fork** offers a clean merge, you can use it after reviewing the comparison. If it reports conflicts, use a PR and resolve them; do not force an update over your configuration.

## Verify the updated version

From the default branch in **Actions → SmartGate sync**:

1. Run `command=preview`, with `apply=false`, `bootstrap=false`. Review the plan and any Summary diagnostics.
2. Run `command=doctor`, with apply and bootstrap disabled. Verify DNS answers, required routes and the journal. A TLS check in Actions does not replace checking the service on your device.
3. For a zero-operation plan with a closed journal, you may use `command=sync`, `apply=true`, `require_no_change=true`, `bootstrap=false`. This rejects any fresh DNS operations or pending recovery; it may save state observations.
4. If the version is compatible and the expected checks pass, restore `SMARTGATE_APPLY_ENABLED=true` if you previously used automatic updates.

A nonzero plan needs separate review before an ordinary sync. Do not raise limits just to make the upgrade appear successful. Explicit state migrations, if required by a future release, must follow that release's instructions; never assume bootstrap is a migration.

## If the upgrade fails

Leave automatic apply disabled and start with the [diagnostic guide](diagnostics.md) and [runbook](runbook.md). For a pending journal, reconcile the saved intent before any new update.

Reverting a code commit does not reverse DNS changes or a state-format migration. Return to the previous code version only if it supports the current state and configuration. DNS rollback is a separate command tied to the exact latest journal; restoring a saved encrypted file over newer state can lose ownership and must not be used as a shortcut.

[First-run guide](../getting-started/quick-start.md) · [State](state.md) · [Release process](../project/releases.md)
