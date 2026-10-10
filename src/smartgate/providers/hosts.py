"""Parse untrusted hosts data and validate selected source snapshots."""

import hashlib
import ipaddress
from dataclasses import replace
from datetime import datetime, timedelta

from smartgate.catalog import ServiceCatalog, normalize_domain
from smartgate.errors import ConfigError, SourceError
from smartgate.models import ProviderConfig, RelayCandidate, RelaySnapshot

MAX_SOURCE_BYTES = 5 * 1024 * 1024
SOURCE_WARNING = "source-provided endpoints; relay ownership and health unverified"


def public_address(value: str) -> str | None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise SourceError("Invalid source IP address") from None
    if not address.is_global or address.is_multicast or address.is_reserved or "%" in value:
        return None
    return str(address)


def parse_hosts(
    text: str, provider: ProviderConfig, source_id: str, now: datetime
) -> RelaySnapshot:
    if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise SourceError("Relay source exceeds 5 MiB")
    if now.tzinfo is None:
        raise SourceError("Snapshot clock must be timezone aware")
    pairs: set[tuple[str, str]] = set()
    total = invalid = 0
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        total += 1
        fields = line.split()
        if len(fields) < 2:
            invalid += 1
            continue
        try:
            ip = public_address(fields[0])
        except SourceError:
            invalid += 1
            continue
        if ip is None:
            continue
        domains: list[str] = []
        bad = False
        for value in fields[1:]:
            try:
                domains.append(normalize_domain(value))
            except ConfigError:
                bad = True
        if bad:
            invalid += 1
        pairs.update((domain, ip) for domain in domains)
    if not total or invalid / total > 0.05:
        raise SourceError("Relay hosts contains too many invalid data lines")
    if not pairs:
        raise SourceError("Relay hosts contains no public endpoints")
    candidates = tuple(
        RelayCandidate(
            provider.id,
            domain,
            ip,
            443,
            "tls_sni_tcp",
            provider.priority,
            source_id,
            provider.region,
        )
        for domain, ip in sorted(pairs)
    )
    warnings = (SOURCE_WARNING,) + ((f"invalid data lines: {invalid}/{total}",) if invalid else ())
    return RelaySnapshot(
        provider.id, now, hashlib.sha256(text.encode("utf-8")).hexdigest(), candidates, warnings
    )


def validate_snapshot(
    snapshot: RelaySnapshot,
    previous: RelaySnapshot | None,
    catalog: ServiceCatalog,
    now: datetime,
) -> RelaySnapshot:
    if now.tzinfo is None or snapshot.fetched_at.tzinfo is None:
        raise SourceError("Snapshot clocks must be timezone aware")
    age = now - snapshot.fetched_at
    if age < timedelta(0) or age > timedelta(hours=24):
        raise SourceError("Source snapshot timestamp is invalid or expired")
    selected = tuple(c for c in snapshot.candidates if catalog.select(c.domain) is not None)
    if not selected:
        raise SourceError("No source endpoints match the service catalog")
    domains = {c.domain for c in selected}
    for service in catalog.services:
        if service.enabled and snapshot.provider_id in service.provider_order:
            if not set(service.required_domains) <= domains:
                raise SourceError("Relay source is missing a required service domain")
    if previous is not None:
        if previous.provider_id != snapshot.provider_id:
            raise SourceError("Previous source belongs to a different provider")
        old = {
            (c.domain, c.ip) for c in previous.candidates if catalog.select(c.domain) is not None
        }
        new = {(c.domain, c.ip) for c in selected}
        if old and len(new) < len(old) * 0.5:
            raise SourceError("Selected relay endpoints dropped by more than 50 percent")
        for service in catalog.services:
            if not service.enabled or snapshot.provider_id not in service.provider_order:
                continue

            def service_pairs(pairs: set[tuple[str, str]], service_id: str) -> set[tuple[str, str]]:
                return {
                    pair
                    for pair in pairs
                    if (policy := catalog.select(pair[0])) is not None and policy.id == service_id
                }

            old_service = service_pairs(old, service.id)
            new_service = service_pairs(new, service.id)
            if old_service and len(new_service) < len(old_service) * 0.5:
                raise SourceError("Service relay endpoints dropped by more than 50 percent")

    warnings = snapshot.warnings
    if age > timedelta(0):
        warnings += ("stale: retain existing routes only",)
    return replace(snapshot, candidates=selected, warnings=tuple(dict.fromkeys(warnings)))
