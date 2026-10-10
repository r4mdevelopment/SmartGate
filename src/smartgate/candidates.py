"""Pure endpoint identity and deterministic candidate ordering."""

import ipaddress

from smartgate.catalog import ServiceCatalog
from smartgate.models import RelayCandidate, RelaySnapshot


def endpoint_key(candidate: RelayCandidate) -> tuple[str, str, str, int, str]:
    return (
        candidate.domain,
        candidate.provider_id,
        candidate.ip,
        candidate.port,
        candidate.transport,
    )


def candidate_pools(
    snapshots: tuple[RelaySnapshot, ...],
    catalog: ServiceCatalog,
) -> dict[str, tuple[RelayCandidate, ...]]:
    grouped: dict[str, list[RelayCandidate]] = {}
    for snapshot in snapshots:
        for candidate in snapshot.candidates:
            service = catalog.select(candidate.domain)
            if service is None or candidate.provider_id not in service.provider_order:
                continue
            try:
                ip = ipaddress.ip_address(candidate.ip)
            except ValueError:
                continue
            if ip.version != 4 or not ip.is_global or ip.is_multicast or ip.is_reserved:
                continue
            if candidate.port != 443 or candidate.transport != "tls_sni_tcp":
                continue
            grouped.setdefault(candidate.domain, []).append(candidate)
    result: dict[str, tuple[RelayCandidate, ...]] = {}
    for domain, values in grouped.items():
        service = catalog.select(domain)
        assert service is not None
        ordered = sorted(
            values,
            key=lambda c: (
                service.provider_order.index(c.provider_id),
                c.priority,
                int(ipaddress.ip_address(c.ip)),
            ),
        )
        seen: set[tuple[str, str, str, int, str]] = set()
        unique: list[RelayCandidate] = []
        for candidate in ordered:
            key = endpoint_key(candidate)
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        result[domain] = tuple(unique)
    return result
