[SmartGate](../../../README.md) / [Documentation](../README.md) / Acceptance checks

[English](acceptance.md) · [Русский](../../ru/project/acceptance.md)

# Acceptance checks

Verification status as of **10 October 2026**. **The original MVP is complete for ChatGPT through NextDNS + GeoHide:** working synchronization, confirmed DNS answers, device acceptance, state persistence between runners and a verified schedule. This does not confirm every catalog service or arbitrary DNS providers.

Live checks were performed in a separately configured deployment. This public source repository does not contain its operational state, credentials or private reports. Its CI runs offline tests; live acceptance must be repeated for your own installation.

## Confirmed capabilities

| Check | Result | Confirmed scope |
| --- | --- | --- |
| Unit / contract / integration | Passed | 696 offline tests; 3 opt-in live tests excluded from ordinary CI |
| Style, types and build | Passed | Ruff, mypy, sdist/wheel build |
| CI Windows 3.12, Linux 3.12/3.13 | Passed | [Public CI workflow](https://github.com/r4mdevelopment/SmartGate/actions/workflows/ci.yml); the matrix also passed before source publication |
| NextDNS API and DoH | Live-verified | Create, replacement through delete/create, delete, read-back, subtree precedence and profile-specific DNS answers; [compatibility boundaries](../development/compatibility.md) |
| Initial bootstrap and repeat synchronization | Passed | 25 rules created; the repeat run confirmed 0 DNS operations and 100 successful DNS checks |
| State between Actions runners | Passed | Two consecutive deployment runs retained ownership and continued the journal without repeating DNS operations |
| ChatGPT on Android | Passed manually | Page, login, message, streaming, file upload and download |
| ChatGPT on a laptop | Confirmed by the user | DNS operation and service access; a separate full feature matrix for each OS was not performed |
| Actual scheduled run | Passed | Scheduled doctor in a separately configured deployment: successful Actions, 100/100 DNS checks, 0 operations and 0 mutations |
| Encrypted Git state and reports | Working | The active ledger is stored in an AES-256-GCM envelope and read by subsequent runners; detailed artifacts are also encrypted |
| Read-only doctor after the state-store update | Passed | Read-only check in a separately configured deployment: successful Actions, 100/100 DNS checks; rules and state revision unchanged |
| Official GeoHide hosts | Read successfully | RU/EU/US directly from the website; 1020 domains in each checked file. Preview with RU/EU proposed 0 DNS operations; all 25 rules and state were retained |
| GeoHide / custom relay | Contract verified | Switching and switching back on fixtures without DNS-layer changes; no real VPS was deployed |
| Recovery, rollback and delayed read-back | Covered by tests | Interruptions, unknown outcomes, manual drift, conflicts and delayed Git refs. All failure variants were deliberately not reproduced on live infrastructure |

## What the warnings mean

The latest confirmed checks have **25 of 27 ChatGPT routes available — about 92.6%**, and the required domain works. Two optional domains, `oaiusercontent.com` and `www.oaiusercontent.com`, did not receive a fresh confirmed route.

The controller retains `degraded` status when some routes are unavailable. Verified operation with these warnings can produce green Actions when all safeguards hold: at least 85% of routes for each service, all required domains, a confirmed journal and successful DNS checks. This does not mean 100% availability. [Status rules](../operations/actions.md#deployment-statuses).

## Current mode and verification boundaries

- With `SMARTGATE_APPLY_ENABLED=false`, the schedule runs **doctor**, without DNS or state writes. The scheduled doctor checks above used this mode.
- With `true`, the schedule runs **sync with apply**. Manual apply and repeat sync have been verified; the latest green scheduled doctor runs do not prove scheduled application of a new nonzero plan after all subsequent changes.
- AdGuard Home and Technitium are reference adapters with local HTTP contracts. Production apply is blocked in code; server live acceptance has not been performed.
- Keys are supplied manually through GitHub Secrets. There is no automatic setup wizard. The detailed plan is read after local decryption; public Summary shows only aggregates.
- Encryption protects current state and detailed reports. It does not remove plaintext previously published in old commits, artifacts or logs. [Privacy boundaries](../operations/privacy.md).

Results apply to the listed runs and scenario. They do not guarantee external relay availability today or operation on every device or in every region. For your installation, perform the [first run](../getting-started/quick-start.md) and [device acceptance](../getting-started/device-check.md).

Further improvements are separate from the required MVP in the [roadmap](roadmap.md).

---

[All documentation](../README.md) · [Home](../../../README.md) · [Actions setup](../operations/actions.md) · [Troubleshooting](../operations/runbook.md)
