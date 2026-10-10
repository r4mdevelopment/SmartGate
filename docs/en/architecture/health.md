[SmartGate](../../../README.md) / [Documentation](../README.md) / Relay TLS/SNI checks

[English](health.md) · [Русский](../../ru/architecture/health.md)

# Relay TLS/SNI checks

The check connects to a candidate's numeric IPv4 on TCP 443. The service domain is passed as server_hostname during the TLS handshake. System resolution of the service name is not used to choose the connection address.

The context is created with ssl.create_default_context. Certificate chain and hostname verification are mandatory: an insecure context is rejected before connecting. An invalid or expired certificate does not produce tls_ok. A successful TCP connection is insufficient.


## On this page

- [Limits](#limits)
- [Selecting candidates to check](#selecting-candidates-to-check)
- [Rechecking a transient failure](#rechecking-a-transient-failure)
- [Meaning of results](#meaning-of-results)
- [Acceptance boundaries](#acceptance-boundaries)

## Limits

One attempt has a shared deadline of up to 4 seconds for TCP connect and the TLS handshake. After connect, the socket is assigned only the remaining timeout. One candidate check contains up to two attempts. No more than eight probes run concurrently; the initial batch includes up to two candidates per domain. The total budget is up to 200 domain/IP pair checks per run, including deferred rechecks. Limits are checked when selecting the batch and before executing it.

Expected network and TLS errors produce failed. An unexpected exception produces unknown without exposing the internal error text. Sockets are closed on failure as well.

## Selecting candidates to check

The currently selected route is checked first. Other candidates are ordered by the service's provider_order, priority, and numeric IPv4. IPv6 remains in source metadata and does not participate in IPv4 probes.

A saved ProbeCursor defines the starting position in the full ordered pool. If all checked candidates for a domain are failed/unknown, advance_probe_cursors moves the cursor past the last checked candidate. The next run checks subsequent addresses instead of repeatedly checking the first two. The function returns new cursors; the controller saves them with observations when `sync --apply` is permitted, including advancement after a failed batch. Read-only preview and doctor do not save cursors.

Batch results preserve input candidate order regardless of thread completion order. With max_candidates_per_domain=1, the current route occupies the only slot; checking a fallback address alongside the current one requires a value of 2.

## Rechecking a transient failure

If the current published address fails to respond because of a network timeout or connection error, the controller waits 5 seconds and rechecks that address once. The recheck occurs before building the final plan and before any DNS or state write. It applies to current routes with an unchanged policy that would otherwise be replaced by a fallback address or remain unconfirmed. Healthy addresses, new candidates, and certificate errors receive no additional recheck.

Only the remainder of the shared `max_pairs` budget is allocated to the recheck: when it is exhausted, the controller uses the initial results and normal safety checks. `probe.attempts` also applies to a recheck, so the current address may receive up to four TCP/TLS attempts per run. The pause occurs once for the entire recheck batch. Offline preview does not perform rechecks.

A successful recheck confirms the current route and permits retaining it. An indeterminate result is not considered successful. For each rechecked address, routing uses the latest result; the original failed probe is not added alongside it as a conflicting observation. DNS checks, the 85% threshold, required domains, and change limits remain unchanged. This mechanism does not retry API writes, recovery, or rollback.

## Meaning of results

- tls_ok — the TLS handshake completed through a specific IP with certificate verification for the required domain.
- failed — the bounded attempts did not confirm a TLS connection.
- unknown — the check could not reliably determine the state.

tls_ok does not confirm account login, message processing, file uploads, the absence of a WAF, or a permitted egress country. The check sends no cookies, login credentials, or application HTTP requests.

vantage identifies where the controller runs: local or github_runner. latency_ms is the duration of a successful TCP/TLS attempt from that point, not the latency experienced by a user on another network. Failed probes are not assigned a successful latency.

## Acceptance boundaries

TLS/SNI checks are part of the current controller: their results are passed to [routing](routing.md), while DNS changes are applied by a separate [reconcile](reconcile.md) layer after safety checks. Successful TLS does not confirm DNS publication or an application scenario.

Route availability must reach at least 85% of the domains of each enabled service, with all required domains confirmed. `doctor` without `--apply` checks sources, TLS, and profile-specific DNS answers without changing DNS or the ledger. Acceptance results, including the separate device check, are described in the [acceptance matrix](../project/acceptance.md).

---

[All documents](../README.md) · [Home](../../../README.md) · [Actions setup](../operations/actions.md) · [Troubleshooting](../operations/runbook.md)
