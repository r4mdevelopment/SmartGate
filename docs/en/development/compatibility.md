[SmartGate](../../../README.md) / [Documentation](../README.md) / DNS compatibility checks

[English](compatibility.md) · [Русский](../../ru/development/compatibility.md)

# DNS compatibility checks

## NextDNS: isolated profile

Status: the basic live check passed on 7 October 2026 against a separate empty profile. This verifies the test target's contract; current operational acceptance is described in [acceptance checks](../project/acceptance.md).

The harness checks create/list/delete/replace, parent/child precedence and A/AAAA/HTTPS answers through the specific test profile's DoH. Confirmed anonymized results are stored in tests/fixtures/nextdns/compatibility.json and tests/fixtures/dns/compatibility.json.

## On this page

- [Isolation](#isolation)
- [Environment setup](#environment-setup)
- [Running the test](#running-the-test)
- [Journal and cleanup](#journal-and-cleanup)
- [Success criteria](#success-criteria)
- [Confirmed contract](#confirmed-contract)
- [Production adapter](#production-adapter)

## Isolation

A separate empty profile is used. The active profile, system DNS, hosts, proxy/VPN, router and browser DNS are not changed.

Before any write, explicitly supply the test profile ID and the protected active profile ID. They must differ. If the active DNS does not use NextDNS, explicitly provide not-nextdns instead of the active profile ID; empty or unknown values are not allowed. A nonempty profile is not modified. Every harness API request uses only the test profile ID.

## Environment setup

Set variables only locally in the terminal that runs the test:

- SMARTGATE_LIVE=1
- NEXTDNS_TEST_ACK=isolated-profile-only
- NEXTDNS_TEST_PROFILE_ID — ID of a separate empty profile.
- NEXTDNS_ACTIVE_PROFILE_ID — ID of the protected active profile; not-nextdns only when another DNS service is confirmed to be in use.
- SMARTGATE_DNS_API_KEY — account key; enter through a secure prompt, never save in a script/config.

Example of secure key entry in PowerShell:

```powershell
$smartGateKey = Read-Host "NextDNS API key" -AsSecureString
$env:SMARTGATE_DNS_API_KEY = [System.Net.NetworkCredential]::new("", $smartGateKey).Password
```

Setting IDs does not change the device's DNS.

## Running the test

```powershell
.\.venv\Scripts\python.exe -m pytest tests/live/test_nextdns_compatibility.py -m live -q
```

Without SMARTGATE_LIVE=1, the test is skipped. A skip does not confirm compatibility.

The test creates temporary rewrites in the isolated profile: a unique example.com subtree for parent/child checks and cloudflare.com to check suppression of real AAAA/HTTPS records. It makes only API/DoH requests, does not connect the device to this profile and makes no HTTP requests to the selected test IPs.

## Journal and cleanup

Before create, intent is saved in .smartgate/compatibility/<test-profile>/journal.json. In finally, only records exactly matching test intent are deleted from the initially empty profile. Ambiguous matches and foreign records are retained.

evidence.json and journal.json contain runtime IDs and are excluded from Git. The key is not saved.

After a crash or cleanup_required, inspect the separate test profile and local journal. Do not delete production records or the entire profile without verification. An incomplete previous journal blocks another test run.

After use, remove SMARTGATE_DNS_API_KEY from the terminal environment:

```powershell
Remove-Item Env:SMARTGATE_DNS_API_KEY
```

## Success criteria

The API roundtrip and DNS assertions must pass, and cleanup must return the test profile to an empty state. Only then are the actual API/DNS contracts confirmed.

## Confirmed contract

GET /profiles/{profile_id}/rewrites returns data containing a record list. POST with name/content returns data with id, name, type and content. DELETE /profiles/{profile_id}/rewrites/{id} returns HTTP 204 without a body; deletion is confirmed by another GET. Replacement has been verified through DELETE and POST. PATCH for rewrites has not been verified.

A parent rewrite extends to subdomains; a more specific child record takes precedence. In the tested profile, an IPv4 rewrite suppressed real AAAA and HTTPS answers for cloudflare.com. After an API write, DNS may return the old value for a while; the test observes answers for up to 60 seconds and blocks further steps if they do not converge.

The full live roundtrip took 58.70 seconds; the final GET confirmed an empty rewrites list. Results apply to the tested profile and date. Another profile requires a new check.

## Production adapter

An additional roundtrip uses NextDnsProvider and DnsVerifier with the same environment guards. Run: `python -m pytest tests/live/test_nextdns_production.py -m live -q`. The journal and evidence are saved in ignored .smartgate/production-compatibility/. This check includes SVCB, while the basic harness remains an independent API capability test. Adapter behavior is described in the [DNS documentation](../architecture/dns.md).

The production roundtrip passed on 8 October 2026 (Europe/Moscow): the fixture records 52.55 seconds for the roundtrip itself, and the historical pytest run took 52.77 seconds. It confirmed 20 DNS checks and cleanup of three remaining test-owned records; the final rewrites list was empty. The anonymized result is stored in tests/fixtures/nextdns/production-roundtrip.json. SVCB was absent from the checked answers; the existence of original SVCB records was not separately characterized.

---

[All documentation](../README.md) · [Home](../../../README.md) · [Actions setup](../operations/actions.md) · [Troubleshooting](../operations/runbook.md)

## Self-hosted reference adapters

AdGuard Home and Technitium HTTP contracts are tested offline, with pinned official API versions. This is not live acceptance or permission to enable production apply.

The optional `tests/live/test_selfhosted_dns_compatibility.py` requires `SMARTGATE_LIVE=1`, `SMARTGATE_DNS_TEST_ACK=isolated-target-only`, separate TEST_API_URL / TEST_NAMESPACE / TEST_DOH_URL values and TEST_PROVIDER (`adguardhome` or `technitium`) in the `SMARTGATE_DNS_` namespace. Set `SMARTGATE_DNS_PROTECTED_FINGERPRINT` for the active target; a match with the test target blocks writes. Test-server credentials are supplied separately through TEST_USERNAME/TEST_PASSWORD or TEST_API_KEY.

TEST_NAMESPACE must be the canonical domain of the test subtree; for Technitium, it is also the name of an existing zone. The AdGuard rewrite list must be empty; Technitium uses an existing dedicated zone containing only baseline apex NS/SOA. The harness saves ignored intent before mutation, confirmed results and cleanup. An unconfirmed write is not automatically claimed; remaining intent requires manual inspection of the disposable target. Checks cover create/list/delete, exact/subtree precedence and A/AAAA/HTTPS/SVCB; replace is covered by offline contract tests, but this self-hosted live harness does not perform it. Without SMARTGATE_LIVE=1, the test is skipped. With live mode enabled, missing required guards or an unsafe target block the test before writes. A successful test does not change registry capabilities: production apply remains prohibited.

Isolated NextDNS tests accept the shared `SMARTGATE_DNS_API_KEY` with temporary verified legacy compatibility. `NEXTDNS_TEST_PROFILE_ID` and `NEXTDNS_ACTIVE_PROFILE_ID` belong to this specific adapter harness; using the active managed profile for write tests is prohibited.

To exclude the active API, the harness also requires `SMARTGATE_DNS_PROTECTED_PROVIDER`, `SMARTGATE_DNS_PROTECTED_API_URL` and, for Technitium, `SMARTGATE_DNS_PROTECTED_NAMESPACE`. It compares the actual write target: API origin for AdGuard Home, and API origin plus zone for Technitium. A DoH address or local instance name does not provide isolation; hostname case, the default HTTPS port and trailing slash are normalized. A single fingerprint of the entire configuration does not replace this check.
