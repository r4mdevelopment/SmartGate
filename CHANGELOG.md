# Changelog

Versioned changes to SmartGate. See the [upgrade guide](docs/en/operations/upgrading.md) before updating a configured fork.

## [Unreleased]

No additional changes recorded.

## [0.1.0] - 2026-10-10

First release of the Smart DNS control-plane.

### Added

- GitHub Actions previews, manual commands and scheduled DNS synchronization.
- Official GeoHide RU/EU/US hosts sources and a manifest adapter for compatible self-hosted relays.
- A configurable service catalog: ChatGPT enabled by default; six additional presets disabled by default.
- Production DNS writes through NextDNS; read-only reference adapters for AdGuard Home and Technitium.
- Bounded TLS/SNI checks, one delayed confirmation of eligible failed current routes, and verified DNS answers.
- Owned-record reconciliation, encrypted state and reports, compare-and-swap checkpoints, recovery and rollback.
- Safe Summary explanations and next steps without publishing domains, addresses or credentials.
- English documentation by default, a complete Russian edition, and automated local link checks.
- Release preparation and instructions for upgrading a fork without replacing its configuration or state.

### Operating limits

- This is an MVP. ChatGPT has completed device acceptance; other service presets require their own checks.
- Scheduled checks run every six hours and may be delayed by GitHub. They are not real-time failover.
- A GitHub TLS check does not prove application access from a user's network.
- Self-hosted relay support expects an already working compatible relay; SmartGate does not deploy that server.
- AdGuard Home and Technitium writes are blocked until their live compatibility requirements are met.

This release does not change the initial defaults: preview before apply, 20 mutations per run, 100 managed records, a 25% replace/delete ratio, and pruning disabled. Initial bootstrap may require a reviewed temporary mutation-limit increase.
