[SmartGate](../../README.md) / Documentation

[English](README.md) · [Русский](../ru/README.md)

# SmartGate documentation

Choose your task: configure the controller, check access, recover a run or connect your own relay.

## Get started

| Document | What you will find |
| --- | --- |
| [First run](getting-started/quick-start.md) | Step-by-step setup for a new installation |
| [GitHub Actions](operations/actions.md) | Secrets, manual runs, schedules and red Deployments |
| [Privacy](operations/privacy.md) | Encryption, keys, a safe fork and detailed reports |
| [CLI and reports](operations/cli.md) | Commands, options, statuses and exit codes |
| [Device check](getting-started/device-check.md) | DNS setup on Android, Windows, Linux, macOS and iPhone; ChatGPT scenarios |
| [Acceptance](project/acceptance.md) | MVP readiness, confirmed checks and limitations |
| [Roadmap](project/roadmap.md) | Completed work and improvement priorities |

## Operations and extensions

| Document | What you will find |
| --- | --- |
| [Error reasons in Summary](operations/diagnostics.md) | Safe codes and next steps |
| [Incident response](operations/runbook.md) | Blocked, degraded, pending and recovery |
| [Switching relays](operations/migration.md) | Moving from GeoHide to your own manifest and back |
| [Relay providers](providers/relays.md) | GeoHide sources and the custom manifest format |
| [DNS synchronization](architecture/reconcile.md) | Change plans, apply, recovery and rollback |

## Inside the controller

| Document | What you will find |
| --- | --- |
| [Architecture](architecture/architecture.md) | Components, interfaces and system boundaries |
| [Route selection](architecture/routing.md) | Priorities, data freshness and route retention |
| [TLS/SNI checks](architecture/health.md) | Endpoint checks, deadlines and candidate budgets |
| [DNS and DoH](architecture/dns.md) | API read-back and profile-specific DNS checks |
| [State and journal](operations/state.md) | Ownership, CAS, Git branch and recovery |

## Development and checks

| Document | What you will find |
| --- | --- |
| [Local development](development/README.md) | Installation, offline tests and quality checks |
| [DNS compatibility](development/compatibility.md) | Isolated live tests and cleanup |
| [Architecture decisions](architecture/decisions.md) | Rationale for key project decisions |

> [!TIP]
> Start with the [main README](../../README.md), then continue to [Actions setup](operations/actions.md). Explore technical details as needed.

---

[Home](../../README.md) · [Start setup](operations/actions.md) · [Troubleshooting](operations/runbook.md)

## DNS providers

[Selection and readiness](providers/dns/README.md) · [NextDNS](providers/dns/nextdns.md) · [AdGuard Home](providers/dns/adguardhome.md) · [Technitium](providers/dns/technitium.md) · [New adapter](providers/dns/adapter-template.md)

## Service catalog

[Enable an available preset or add your own service](providers/services.md).

## Releases and upgrades

[Upgrade a configured fork](operations/upgrading.md) · [Prepare a release](project/releases.md) · [Changelog](../../CHANGELOG.md)
