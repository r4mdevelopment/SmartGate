[SmartGate](../../../README.md) / [Documentation](../README.md) / GitHub Actions

[English](actions.md) · [Русский](../../ru/operations/actions.md)

# GitHub Actions

The repository has two workflows: `CI` checks the code without credentials; `SmartGate sync` runs the controller on the default branch. Automatic application is disabled by default. With `SMARTGATE_APPLY_ENABLED=false`, scheduled runs execute `doctor`: they check current DNS responses and route availability without changing DNS records or state. With `true`, the schedule runs `sync --apply`.


## On this page

- [Setup](#setup)
- [Manual runs](#manual-runs)
- [Upgrading a legacy ledger](#upgrading-a-legacy-ledger)
- [Schedule and stopping](#schedule-and-stopping)
- [State, reports and permissions](#state-reports-and-permissions)
- [Deployment statuses](#deployment-statuses)
- [Failed runs and quotas](#failed-runs-and-quotas)
- [Route availability threshold](#route-availability-threshold)
- [Encrypting existing state](#encrypting-existing-state)

## Setup

First create your own copy of the repository: **Fork → Copy the main branch only**. Do not copy someone else's state branch. Open **Actions** in your copy and enable workflows if they are disabled. For a private installation, use a separate private copy of the source code.

Choose a [DNS adapter](../providers/dns/README.md) and prepare a separate target following its guide. The initial configuration uses one working adapter; self-hosted reference adapters are available for inspection, but not for production apply.

In **Settings → Environments**, create the `smartgate` Environment. Prepare the [state key and its backup](privacy.md#prepare-a-key). Add the selected provider's settings and the encryption key:

| Type | Name | When required |
| --- | --- | --- |
| Secret | `SMARTGATE_STATE_ENCRYPTION_KEY` | For live Git state: 32 random bytes in Base64 |
| Secret | `SMARTGATE_DNS_API_KEY` | API key or Bearer token |
| Secret | `SMARTGATE_DNS_USERNAME` | Basic authentication |
| Secret | `SMARTGATE_DNS_PASSWORD` | Basic authentication |
| Secret | `SMARTGATE_DNS_PROFILE_ID` | Adapter profile when a profile ID is required |
| Secret | `SMARTGATE_DNS_API_URL` | HTTPS origin of your own API |
| Secret | `SMARTGATE_DNS_NAMESPACE` | Dedicated scope or existing zone in your own DNS |
| Secret | `SMARTGATE_DNS_DOH_URL` | HTTPS DoH endpoint of your own DNS |
| Variable | `SMARTGATE_APPLY_ENABLED` | `false` until manual acceptance checks are complete |

Update the DNS block in `config/smartgate.actions.toml` using the [selected adapter's example](../../../config/dns). Keep the `git_branch` state provider, `smartgate-state` branch and service catalog. A server with unverified capabilities remains read-only regardless of `sync.enabled` and workflow inputs.

Store DNS identifiers and API/DoH URLs in **Secrets** too: GitHub masks them in its environment output before the controller starts. Previous Variables remain a compatibility fallback, but their values may appear in logs. Keep the same value when moving it to a Secret; the DNS target does not change. Matching old aliases are temporarily allowed; conflicting values block the run.

Actions supplies `GITHUB_TOKEN` itself. Do not add it as your own secret. Restrict the Environment to the default branch and configure required reviewers if needed.

The workflow configuration is `config/smartgate.actions.toml`; `sync.enabled=true` permits explicitly confirmed manual apply; automatic writes additionally require `SMARTGATE_APPLY_ENABLED=true`. GitBranchStateStore keeps state in a separate orphan branch, `smartgate-state`. Reading does not create a missing branch; only explicit bootstrap can initialize it.

Do not connect a device to this DNS target until you have checked the result and relay readiness. The CLI does not change your computer's DNS.

## Manual runs

Open Actions → SmartGate sync → Run workflow on the default branch. Start with `command=preview`, `apply=false`, `bootstrap=false`. Review the aggregates in Summary. To inspect domains and the plan, download `report.enc.json` and [decrypt it locally](privacy.md#reading-a-detailed-report).

For the first apply:

1. Check the empty DNS target, domains and changes in preview.
2. Check that `sync.enabled=true` in `config/smartgate.actions.toml` on the default branch. This configuration permits explicitly confirmed manual apply; leave the schedule variable at `false`.
3. If the operation count exceeds `max_mutations=20`, review the plan, then temporarily change this limit in `config/smartgate.actions.toml` on the default branch of your copy. For example, 30 is enough for 25 create operations. Repeat preview, resolve other blocking reasons, and only then run `command=sync`, `apply=true`, `bootstrap=true`. Bootstrap does not bypass the operation limit. Restore the usual limit of 20 after the first apply.
4. For a repeat acceptance check, select `command=sync`, `apply=true`, `bootstrap=false`, **`require_no_change=true`**. The expected result is no_change when routes are healthy and DNS matches. If the fresh plan contains even one operation, the run ends as blocked without changing DNS or state.

### Checking without DNS changes

`require_no_change=true` is available only for manual `sync` with `apply=true` and no bootstrap. The controller checks the plan within the run itself: even a zero-operation preview does not guarantee that sources and routes will remain the same until the next sync. Any create, replace or delete is blocked before preflight and writes. An unfinished journal also blocks the check; automatic recovery does not run.

With a zero-operation plan, normal API and DNS checks run. The controller may update observations and route metadata in the state branch; it does not change DNS records. Check failures and the 85% availability threshold retain their usual meaning. The normal schedule uses `require_no_change=false`.

If the check is blocked, review the new plan and authorize necessary changes separately; applying them requires a normal manual sync. Do not disable protection just to obtain a green status.

Manual `apply=true` permits writes independently of the automatic schedule variable. You can therefore keep `SMARTGATE_APPLY_ENABLED=false` throughout manual acceptance checks. Recover/rollback require the same explicit manual authorization; the configuration must permit sync.

Topic and state branches do not execute the sync job that receives secrets. The workflow must exist on the default branch; pushing a working branch does not itself enable the schedule.

## Upgrading a legacy ledger

For an existing installation, the new DNS configuration schema requires an explicit state transition. Stop automatic apply and wait for active and queued writers to finish before changing workflow concurrency. Save the old settings, check API records and the ledger, then create provider-neutral Secrets/Variables. The workflow temporarily accepts the old names only to migrate the current adapter; a conflict between old and new values blocks the run.

On the default branch, run `command=state-upgrade`, `apply=false`, `bootstrap=false`; after reviewing it, run the same command with `apply=true`. The new file and removal of the old path are saved in one CAS operation. This command does not change DNS records. Recover an unfinished journal with the previous configuration first. See [format and upgrade](state.md#upgrading-old-state).

After read-back, repeat doctor and run a fresh preview. Normal sync is permitted only after reviewing the fresh plan. Remove the previous Secrets and restore the schedule after confirming the transition. Upgrade cannot run on a schedule or with bootstrap.

## Schedule and stopping

Cron `17 */6 * * *` runs the controller at 00:17, 06:17, 12:17 and 18:17 UTC. With `SMARTGATE_APPLY_ENABLED=false`, it runs read-only `doctor`, checking DNS for managed records. Before the first successful bootstrap, it does not confirm readiness and may end as `degraded`: the schedule does not initialize missing state. After checking manual apply, a repeat no-op, profile DNS responses and the application scenario, you can set the variable to `true`. `sync.enabled=true` is also required.

To stop automatic writes, set the variable back to `false`. To block DNS apply, set `sync.enabled=false`. The state-upgrade command separately requires explicit manual authorization and changes only state. To stop all reads and probes, disable the workflow in Actions.

GitHub runs the schedule on the default branch; a run may be delayed or skipped. In a public repository, schedules are disabled after 60 days of inactivity. This does not provide real-time failover or an SLA. See [GitHub schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

Runs share a repository concurrency group with `cancel-in-progress: false`; a new run does not cancel an active one. State CAS and the execution lease protect against another writer outside Actions. GitHub may replace a queued run with a newer one: [concurrency rules](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency).

## State, reports and permissions

The sync job receives `contents: write` to save state; CI receives read access only. GitHub permissions are not limited to one branch: the adapter and workflow policy enforce the state branch restriction. Do not use `pull_request_target` or run this job with code from a fork PR.

**In a public repository, readers can access the state branch, but its contents are encrypted.** Route details are accessible with the key; configuration, sizes and commit times remain visible. Before publication, check old plaintext history and previous Actions outputs. See [Privacy](privacy.md).

Summary contains aggregates. The artifact includes safe `report.json`, `report.md` and encrypted `report.enc.json`. It is retained for 7 days; it excludes state, `.env`, credentials and raw evidence. A fallback report is created if an error occurs before the CLI starts. After a hard timeout or cancellation, report upload may not finish: check durable state and workflow logs.

The job timeout is 25 minutes; the controller execution lease is 20 minutes. Before recovery, make sure the previous process has stopped; Actions does not perform early takeover automatically.

## Deployment statuses

> [!NOTE]
> A job using the `smartgate` Environment creates a linked Deployment in GitHub. It records controller execution; no separate website is deployed here.

The CLI retains exit code **3** for `degraded`. Actions additionally recognizes successful synchronization with warnings: if required routes are available, apply is complete, the journal is `verified`, all DNS checks pass and there are no errors, unavailable optional routes produce a green workflow with a warning in Summary only when at least **85% of the domains in each enabled service** are available. The report retains `status=degraded`, `exit_code=3` and shows `workflow_exit_code=0`. After a completed rollback, the `rolled_back` journal status is also allowed if a new sync has an empty plan, explicitly confirms zero changes and passes all other checks. This checks current DNS without new operations; the previous journal status is not rewritten.

A `doctor` check can also be green with optional warnings: the fresh plan must be empty, the journal complete, each service's availability at least 85%, and all current DNS checks successful. The controller rereads the API and state after verification; a change to either snapshot blocks the result. The journal is not rewritten. Ordinary `preview` and `sync` without apply still do not confirm successful DNS verification.

If the current relay has a network failure, the controller automatically [repeats the TLS check after a pause](../architecture/health.md#rechecking-a-transient-failure). This is part of the same scheduled run; a separate manual run is not required to confirm a transient failure. The retry does not change success rules or repeat DNS writes.

Source/API failures, required route failures, failed DNS checks, uncertain apply and read-only previews with problems remain red. Old failed runs are not retroactively rewritten.

| Situation | Next action |
| --- | --- |
| `verified`, successful DNS checks, optional routes unavailable | Check service availability and warnings; existing routes may work |
| `partial` or unfinished journal | Follow the [runbook](runbook.md) to recover, excluding any parallel writer |
| `blocked` | Investigate conflicts, limits or preconditions; apply is not authorized |

Do not turn every nonzero exit code into success: this hides real errors and uncertain mutations.

## Failed runs and quotas

First read the report's status and exit code. Pending/partial requires recovery; an ownership conflict requires drift investigation. A new sync with apply first recovers pending work and ends the command; the following sync builds a new plan. Failed workflow notifications use GitHub's standard settings; the controller does not send messages.

Check the selected DNS provider's quotas and limits in its [guide](../providers/dns/README.md). Controller DNS checks also count toward the service's usage.

DNS API transport is limited to 400 calls per run; changes are serialized. An adapter may impose additional rate limits. CI checks locked installation and the offline suite on Windows/Python 3.12 and Linux/Python 3.12/3.13. Actions references are pinned to full upstream commit SHAs.

GitHub state transport is also limited to 400 calls. The budget for the entire batch is checked before writes; an oversized batch is blocked before bootstrap and DNS changes. Caching immutable Git objects reduces request counts while retaining fresh branch ref checks and CAS; see [StateStore](state.md).


### Route availability threshold

For each enabled service, the controller counts unique domains from current decisions, saved sources and managed records, plus explicitly required domains. A domain is working if it has a selected or saved confirmed route. Unknown, failed and routes retained with degraded status do not count as successful. Disabled sets are excluded.

The **85%** threshold uses exact counts before rounding: 17/20 passes, 16/20 fails; 11/13 = 84.62% fails. Each service is evaluated separately; when scopes overlap, a shared domain belongs to each applicable set, accounting for its exclusions. All required domains must work; the API, journal and published DNS records must pass complete verification. The percentage affects how successful sync is displayed; it does not authorize unsafe apply.

Summary and JSON contain the working domain count, total count and percentage. This is coverage of known routes, not a guarantee of every application feature or every possible subdomain. Removing a domain from a new source does not immediately remove it from the denominator while it remains known to a saved source or ownership. A completely unknown service does not receive a green result.

## Encrypting existing state

Run manual `state-encrypt` first with `apply=false`, then, after review, with `apply=true`. It does not change DNS, does not run on a schedule and is incompatible with bootstrap, rollback ID and `require_no_change`. The new version requires a key and rejects plaintext through ordinary commands. See the [transition procedure](privacy.md#migrating-an-existing-installation).

---

[All documentation](../README.md) · [Home](../../../README.md) · [Actions setup](actions.md) · [Troubleshooting](runbook.md)


## Manual key rotation

`state-rotate` checks or re-encrypts a single bound Git-state document. It requires paused automatic apply and a temporary new-key Secret. The command creates an encrypted backup before a CAS update; the primary Secret switch remains manual. [Full procedure](key-rotation.md).

## Route monitoring

An optional [short route monitor](monitoring.md) can collect DNS/TLS/HTTP observations every 30 minutes without changing DNS or creating Deployments. Enable its repository variable only after a successful manual check; account usage applies.
