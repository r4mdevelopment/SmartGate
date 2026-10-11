[SmartGate](../../../README.md) / [Documentation](../README.md) / Roadmap

[English](roadmap.md) · [Русский](../../ru/project/roadmap.md)

# Roadmap

## What is ready

**The MVP is complete in the verified ChatGPT scenario through NextDNS + GeoHide.** The controller runs in GitHub Actions, reads official hosts, checks routes, synchronizes DNS and retains encrypted state. Repeat runs without changes, operation between runners, the schedule and device access have been confirmed. [Acceptance matrix and its limitations](acceptance.md).

Device access to Claude, Gemini, Spotify, Notion, DeepL and Cursor without geographic access errors has also been confirmed by a user. This covers access in the tested configuration; it does not certify every function or the complete domain sets. The user has accepted the service results and closed further manual acceptance as a planned task. Presets remain disabled by default.

A new DNS adapter and a new relay are connected independently. A custom relay is described by a manifest; a VPS IP alone without a working relay is insufficient. AdGuard Home and Technitium currently support reading only.

## Priorities after the MVP

Safe [Summary diagnostics](../operations/diagnostics.md), grouped bilingual documentation, the [release workflow](releases.md), a changelog and the [fork upgrade guide](../operations/upgrading.md) are implemented. The first tagged release, [v0.1.0](https://github.com/r4mdevelopment/SmartGate/releases/tag/v0.1.0), is published; the release workflow and package artifacts have been verified.

The following operational improvements are implemented and checked:

| Status | Improvement | Benefit for the user |
| --- | --- | --- |
| Complete | [Encryption-key rotation](../operations/key-rotation.md) | Back up state, require a closed journal, re-encrypt with the new key and verify reading before completing the switch; never silently replace a Secret |
| Complete | [Isolated recovery drills](../operations/recovery-drills.md) | Backup recovery, schema 1 → 2 state upgrade and key rotation checked on a disposable profile; owned DNS records unchanged. The published v0.1.0 decoder also reads rotated state |

Additional DNS adapters can develop through community contributions. They require their own API/DNS compatibility checks and record-ownership evidence; a successful mock test or a single harness run does not automatically enable production writes.

## Operational limitations

- Periodic checks do not provide instant switching. GitHub Actions, external relays, APIs and device DNS caches affect delays.
- GitHub may delay schedules or disable them under conditions described in the [Actions guide](../operations/actions.md#failed-runs-and-quotas). Detecting a stopped schedule requires observation outside that stopped workflow; the project has no separate external monitoring.
- A TLS check from a runner does not prove application operation from every user's network. New services and a custom VPS need device checks.
- Initial setup remains manual through GitHub. Local decryption of a detailed report still requires Python.

Diagnostics and installation reproducibility are improved first, followed by expansion of the verified scope. New directions must preserve ownership, explicit write authorization and encryption.

---

[All documentation](../README.md) · [Home](../../../README.md) · [Acceptance](acceptance.md) · [Troubleshooting](../operations/runbook.md)
