[SmartGate](../../../README.md) / [Documentation](../README.md) / Architectural decisions

[English](decisions.md) · [Русский](../../ru/architecture/decisions.md)

# Architectural decisions

## ADR-001 — periodic Python control plane

The controller fetches small sources, performs HTTPS/DNS/TLS checks, and calculates DNS changes. The controller itself does not need an always-running server. Traffic is forwarded by an external relay — GeoHide or a server of your own that is already configured.

Python 3.12/3.13 is used. HTTPX and dnspython provide network operations; TOML, the CLI, models, and TLS use the standard library. Dependencies are pinned in lock files with hashes.

Your own relay is a separate component and may use another language without changing the controller interfaces.

## ADR-002 — independent DNS and relay adapters

The DNS layer accepts DesiredRewrite and does not depend on the origin of the IP. Relay providers return the same normalized models. This allows route sources to change without redesigning synchronization or the catalog.

## ADR-003 — read-before-write and ownership

A DNS profile may contain unrelated rewrites. The controller modifies only records with confirmed IDs and matching preconditions. The journal is necessary for recovery after non-atomic API operations.

---

[All documents](../README.md) · [Home](../../../README.md) · [Actions setup](../operations/actions.md) · [Troubleshooting](../operations/runbook.md)
