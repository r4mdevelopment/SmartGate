[SmartGate](../../../README.md) / [Documentation](../README.md) / Command-line interface

[English](cli.md) · [Русский](../../ru/operations/cli.md)

# Command-line interface

The CLI is available as `python -m smartgate` and as `smartgate` after installing the package. Controller commands accept `--config PATH`; `report-decrypt` instead has separate file and report binding parameters. The CLI does not change the device's DNS settings.


## On this page

- [Commands](#commands)
- [Offline checks](#offline-checks)
- [Live preview](#live-preview)
- [First apply](#first-apply)
- [Reports and exit codes](#reports-and-exit-codes)
- [Acceptance without DNS changes](#acceptance-without-dns-changes)
- [Upgrading state](#upgrading-state)
- [Explicit state encryption](#explicit-state-encryption)

## Commands

| Command | Purpose |
| --- | --- |
| `validate` | Check TOML and the catalog without network access or keys |
| `preview` | Fetch sources, check TLS and show a fresh plan |
| `sync` | Build a plan; apply only with `--apply` |
| `doctor` | Read the profile, check routes and DNS for owned records |
| `state-encrypt` | Explicitly encrypt existing Git state without DNS changes |
| `report-decrypt` | Locally decrypt an artifact into a new file without network requests |
| `recover` | Reconcile the saved journal with the profile |
| `rollback --run-id ID` | Roll back the latest saved journal with the specified ID |
| `state-upgrade` | Check a legacy ledger transition; with `--apply`, update only the state format |

Without `--apply`, there are no DNS writes or state saves. With a pending journal, read-only inspection returns partial status, while `sync --apply` performs only recovery; a new plan requires a separate run. This sequence separates recovery from new route changes.

`sync.enabled=false` blocks DNS apply, including recovery and rollback. `state-upgrade --apply` and `state-encrypt --apply` change only the ledger and do not require DNS apply to be enabled. DNS writes require both configuration permission and `--apply`. The `preview`, `doctor` and `validate` commands do not allow apply.

## Offline checks

```console
python -m smartgate validate --config config/smartgate.example.toml
python -m smartgate preview --config config/smartgate.example.toml --offline --fixtures tests/fixtures --report-dir .smartgate/reports
```

Offline mode uses a curated hosts fixture, empty DNS/state and explicitly simulated TLS results. The source timestamp is adjusted to the demonstration time. These results show program behavior and do not confirm IP availability. The report contains `offline: true`; offline apply is prohibited. The same fixture substitutes for each configured relay to check configuration without network access.

## Live preview

Choose a [DNS adapter example](../../../config/dns), copy it to a local `config/smartgate.toml` and set the required variables from its guide. Paths in TOML are relative to the configuration file: when moving an example from `config/dns/` to `config/`, change `catalog_path` to `"services.toml"` and `[state].path` to `"../.smartgate/state.json"`. Then run `validate` with the new file. Do not store credentials in TOML.

Example for the NextDNS adapter in PowerShell, without printing the key:

```powershell
$env:SMARTGATE_DNS_PROFILE_ID = Read-Host 'NextDNS profile ID'
$env:SMARTGATE_DNS_API_KEY = [Net.NetworkCredential]::new('', (Read-Host 'NextDNS API key' -AsSecureString)).Password
python -m smartgate preview --config config/smartgate.toml --report-dir .smartgate/reports
```

Preview reads the selected DNS provider's API and state, fetches relay sources and opens verified TLS connections directly to IP addresses. It does not require switching system DNS. `doctor` additionally queries the selected target's DoH endpoint through separate HTTPS requests.

## First apply

Use an adapter approved for production apply and a separate empty DNS target; review the domain catalog and preview. Also check `max_mutations`, `max_records` and blocking reasons. If the first plan exceeds `max_mutations=20`, change the limit only after reviewing the specific operations and repeat preview; restore the usual limit after the first successful apply. Then set `sync.enabled=true` in your configuration:

```console
python -m smartgate sync --config config/smartgate.toml --apply --bootstrap --report-dir .smartgate/reports
python -m smartgate sync --config config/smartgate.toml --apply --require-no-change --report-dir .smartgate/reports
```

`--bootstrap` is required only to explicitly initialize an empty profile with no ownership. Remove the flag after the first run. If candidates fail TLS checks, bootstrap may save the source/cursor for the next bounded check, but it creates no DNS records and returns degraded.

Without this save, a bounded batch could repeatedly check the same nonworking IPs. Ordinary preview does not change cursors. Saved snapshots do not become fresh again when a download fails; after 24 hours, they are not used to select routes. The last valid snapshot remains the baseline for comparing the quality of the next source.

For a profile managed by Actions, local commands must also use GitBranchStateStore:

```powershell
$env:GITHUB_REPOSITORY = 'owner/SmartGate'
$env:GITHUB_TOKEN = [Net.NetworkCredential]::new('', (Read-Host 'GitHub token' -AsSecureString)).Password
$env:SMARTGATE_STATE_ENCRYPTION_KEY = [Net.NetworkCredential]::new('', (Read-Host 'State encryption key' -AsSecureString)).Password
$env:GITHUB_RUN_ID = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString()
$env:GITHUB_RUN_ATTEMPT = '1'
python -m smartgate preview --config config/smartgate.actions.toml --state-provider git_branch --report-dir .smartgate/reports
```

Also set the DNS variables for that same installation. The key must match the Environment Secret. `GITHUB_RUN_ID` and `GITHUB_RUN_ATTEMPT` are required even locally to bind the encrypted report; save their values for `report-decrypt` and choose a new positive numeric ID for each local run. The token must permit repository reads and state writes during apply. Do not use an independent local ledger for the same profile.

## Reports and exit codes

`--report-dir PATH` saves reports. In Actions and live Git state, public `report.json`/`report.md` contain status and aggregates; detailed JSON is saved in `report.enc.json`, and full Markdown is not published. `--private-report` explicitly enables the same projection. Configuration and initialization errors are also displayed without arbitrary strings.

Ordinary local reports may contain domains and IPs: keep them outside Git. Details of sources, checks, the plan and `journal_run_id` are available in the decrypted artifact. See [key and local decryption](privacy.md#reading-a-detailed-report).

| Code | Meaning |
| --- | --- |
| 0 | Preview, no_change or applied |
| 2 | Invalid arguments, configuration or required environment variables |
| 3 | Degraded: source, TLS or DNS checks do not confirm full functionality |
| 4 | Safety, ownership or CAS block |
| 5 | External error before confirmed mutations |
| 6 | Partial, pending journal or error after a possible write |

A missing usable required-domain route does not return code 0. Successful DNS/TLS checks do not replace checking the application from the user's device.


## Acceptance without DNS changes

For live `sync --apply`, add `--require-no-change`. A fresh plan containing any DNS operation, or an unfinished journal, is blocked before writes. The flag is incompatible with bootstrap, offline mode, read-only commands and recovery/rollback. With a zero-operation plan, DNS and API checks run; state observations may be updated. In Actions, the corresponding input is `require_no_change=true`.

## Upgrading state

`state-upgrade --config <path>` checks the transition of a legacy ledger to a format bound to its target. Adding `--apply` changes only state, with backup/CAS and read-back, without DNS mutations. The command is allowed only in live mode, without bootstrap. Recover an unfinished journal with the previous configuration first. For the main user installation, use the manual command of the same name in Actions; the local CLI is an additional operating method.

## Explicit state encryption

`state-encrypt --config <path>` checks an existing bound ledger through the API without writes. `--apply` moves the same document to encrypted Git storage with CAS/read-back; DNS does not change. A closed journal, Git provider and key are required. Bootstrap, offline mode, takeover, rollback ID and `--require-no-change` are prohibited. Schema 1 must first be upgraded to schema 2 using the previous controller. See the [complete procedure](privacy.md#migrating-an-existing-installation).

---

[All documentation](../README.md) · [Home](../../../README.md) · [Actions setup](actions.md) · [Troubleshooting](runbook.md)


## State key rotation

`state-rotate` requires live bound Git state, `SMARTGATE_STATE_NEW_ENCRYPTION_KEY` and paused automatic apply. Without `--apply` it only checks; with `--apply` it backs up and re-encrypts the ledger without DNS writes. Bootstrap, offline, rollback ID and no-change acceptance flags are not accepted. See [key rotation](key-rotation.md).

## Emergency-only inspection

`preview --emergency-only` inspects only an eligible mass failure; `sync --emergency-only --apply` uses the restricted replacement plan. Bootstrap, offline mode, recovery, rollback and no-change acceptance cannot be combined with it. In Actions, select `emergency_only=true` with `command=preview` for read-only inspection. Emergency apply also requires the Environment apply safety switch. See [conditions, opt-in and limits](failover.md).
