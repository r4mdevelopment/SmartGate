[SmartGate](../../../README.md) / [Documentation](../README.md) / Route monitoring

[English](monitoring.md) · [Русский](../../ru/operations/monitoring.md)

# Route monitoring

**SmartGate monitor** records short DNS, TLS and HTTP observations of published routes. It does not change DNS records, select replacement addresses, recover a journal or save state. The normal six-hour sync remains independent.

The observing job remains read-only. If separately enabled, this workflow may run a gated [emergency writer](failover.md) after a complete mass-failure observation. A normal manual run leaves `emergency_apply` unchecked and cannot launch it.

## Enable monitoring

1. Complete the normal setup, including encrypted state and a successful sync. The monitor uses the existing `smartgate` Environment secrets and the same DNS adapter configuration.
2. Open **Actions → SmartGate monitor → Run workflow** on the default branch. Inspect Summary and download the encrypted artifact if needed.
3. In **Settings → Secrets and variables → Actions → Variables**, create the **repository variable** `SMARTGATE_MONITOR_ENABLED` with value `true`.

Scheduled checks run at minutes **11 and 41 of every hour**, in UTC. Set the repository variable to `false` to pause them. Manual checks remain available. New forks have scheduled monitoring disabled until they opt in.

The job has `contents: read` and references the Environment with `deployment: false`: it can read the existing secrets without creating a Deployment. It cannot push the state branch. No extra token or key is required. Environments with custom deployment protection rules are incompatible with this mode; use a supported Environment configuration before enabling it. [GitHub documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/deploy-to-environment).

## What is checked

The monitor reads schema-2 encrypted ownership state and the DNS API. It checks only matching owned records, up to 100, and requires the correct target binding and a completed journal. Foreign DNS records are not probed.

For each route it requests the configured DoH endpoint's **A** answer and makes an anonymous **GET /** through the stored numeric IPv4 address. TLS verifies the certificate for the original domain through SNI. It does not follow redirects, send authentication or cookies, retain response bodies or store arbitrary headers.

There are at most eight concurrent route checks. HTTP has a four-second total deadline and a 16 KiB header limit, including at most four informational responses. DoH uses bounded transport phase timeouts and checks elapsed time after headers and each body chunk; phase transitions can make a request exceed four seconds. New checks stop starting after the 30-second collection budget expires; in-flight transport phases may finish later. Unchecked routes are explicitly reported. Setup and read-back are outside this budget; the Actions job has a three-minute timeout.

The API and state are reread after the observations. A concurrent change produces an `incomplete` report with `snapshot_changed`; repeat after the sync finishes. Monitoring has its own concurrency group and does not cancel a queued sync.

## Read the results

| Result | Meaning |
| --- | --- |
| `observed` | All routes had the expected A answer and an HTTP 2xx/3xx response |
| `observed_with_warnings` | Collection completed with DNS differences, TLS errors or HTTP warnings |
| `incomplete` | Some routes were not checked, a snapshot changed or collection failed |

HTTP observations distinguish `response`, `challenge`, `authorization_required`, `http_rejected`, `rate_limited`, `server_error`, transport/certificate errors and malformed responses. Cloudflare challenges and HTTP 403 responses do not trigger failover.

A successful Actions job means observations were collected; **it does not certify browser login or application access**. Recorded endpoint errors remain visible as warnings. Setup, ownership or encryption failures make the job fail. Monitoring does not use the sync's 85% availability threshold and does not create Deployment entries.

Summary and `monitor.json` contain aggregates only. `monitor.enc.json` contains domains, addresses, DNS outcomes, HTTP status, classification, timestamps and elapsed time. Artifacts are retained for **seven days**. No device browsing history is collected.

Decrypt `monitor.enc.json` with the existing [report-decrypt command](privacy.md#reading-a-detailed-report), substituting its filename and the monitor run's ID/attempt. The report uses the existing state encryption key and authenticated repository/run binding. Never post the decrypted report publicly without reviewing it.

## Frequency and cost

A 30-minute schedule creates about **1,440 runs in a 30-day month**. At one billed minute per run, monitoring alone uses about 1,440 minutes; at two it uses about 2,880, before sync and CI. These are estimates, not a runtime guarantee. Check account usage after the first runs; disable monitoring or reduce the cron frequency if needed.

Private repositories share the owner's included Actions minutes and artifact storage. Standard hosted runners in public repositories have free compute, while storage policies still apply. [GitHub billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

For 25 routes, the monitor also makes up to **1,200 DNS queries per day**, plus sync and device queries. HTTP requests are additional relay traffic. Adjust frequency if those costs are unsuitable.

GitHub may delay or omit scheduled jobs. A short outage can occur between observations. Checks originate from a GitHub runner, not the user's network, and cannot reconstruct a past outage before monitoring was enabled. [Schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
