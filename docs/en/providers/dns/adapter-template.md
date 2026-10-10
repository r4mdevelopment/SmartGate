[SmartGate](../../../../README.md) / [Documentation](../../README.md) / New DNS adapter

[English](adapter-template.md) · [Русский](../../../ru/providers/dns/adapter-template.md)

# New DNS adapter

Implement `DnsProvider`: `list_records`, `create`, `delete`, `replace`. List must preserve foreign records or block an ambiguous response rather than hide it. A mutation is confirmed by read-back; an unknown outcome requires RecoveryRequired and is not retried automatically.

## Minimal interface

```python
from smartgate.models import DesiredRewrite, DnsRecord


class ExampleDnsProvider:
    def list_records(self) -> tuple[DnsRecord, ...]:
        # Decode the server's complete scoped snapshot, preserving foreign records.
        raise NotImplementedError

    def create(self, record: DesiredRewrite) -> DnsRecord:
        # Validate public IPv4, issue one mutation, confirm exact read-back.
        raise NotImplementedError

    def delete(self, record: DnsRecord) -> None:
        # Confirm the before identity and confirm its absence after deletion.
        raise NotImplementedError

    def replace(self, before: DnsRecord, after: DesiredRewrite) -> DnsRecord:
        # Keep the domain; document atomicity and uncertain intermediate states.
        raise NotImplementedError
```

This is a contract sketch, not a registered production provider. Add typed auth/connection/verify configuration and an explicit binding in `dns/registry.py`. Return a `DnsBundle` with DnsTarget, built-in DnsCapabilities and an endpoint-bound verifier. Credentials are resolved only through the required env variables; the transport limits origin, TLS, redirects, size, time and request count.

## Capability verification

| Property | What to confirm |
| --- | --- |
| Identity | A persistent ID or ownership mechanism; an identical value after delete/recreate does not prove ownership |
| Scope | Target isolation, foreign records, duplicates, concurrent writer |
| Replace | Atomic or delete/create, read-back, recovery after a lost response |
| DNS | A, AAAA, HTTPS, SVCB, exact/subtree precedence, exclusions |
| State | API origin/scope/DoH fingerprint; drift blocks plan, apply, recovery and rollback |

Start with `production_apply=False`. Contract tests use simulated HTTP; live checks require a separate empty target, explicit write authorization and a cleanup ledger. The readiness table must distinguish implemented, contract-tested and live-verified. A user flag in TOML cannot raise capabilities.

Reference examples: [AdGuard Home](adguardhome.md) and [Technitium](technitium.md); the production example is [NextDNS](nextdns.md). The shared suite `tests/contract/test_dns_provider_contract.py` checks CRUD read-back, foreign record preservation, stale preconditions and rejection of nonpublic IPv4. Transport errors, ambiguity, DNS semantics and recovery are additionally verified by adapter-specific contract/unit/integration tests. The live harness itself does not raise capabilities in the registry.
