"""Pure, stable routing over validated sources, current-run probes and ownership."""

import hashlib
import ipaddress
import json
from datetime import datetime, timedelta

from smartgate.candidates import candidate_pools, endpoint_key
from smartgate.catalog import ServiceCatalog, normalize_domain
from smartgate.errors import SafetyBlocked
from smartgate.models import (
    DesiredRewrite,
    ManagedRecord,
    ProbeResult,
    ProbeStatus,
    RelayCandidate,
    RelaySnapshot,
    RouteDecision,
    Selection,
    ServicePolicy,
    StateDocument,
)


def routing_policy_digest(service: ServicePolicy) -> str:
    """Hash routing policy, preserving the meaningful order of providers."""
    payload = {
        "id": service.id,
        "enabled": service.enabled,
        "include_suffixes": sorted(service.include_suffixes),
        "required_domains": sorted(service.required_domains),
        "exclude_suffixes": sorted(service.exclude_suffixes),
        "provider_order": service.provider_order,
        "allow_subtree_rewrites": service.allow_subtree_rewrites,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _valid_endpoint(candidate: RelayCandidate, domain: str) -> bool:
    try:
        address = ipaddress.ip_address(candidate.ip)
    except ValueError:
        return False
    return (
        candidate.domain == domain
        and normalize_domain(domain) == domain
        and address.version == 4
        and address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and candidate.port == 443
        and candidate.transport == "tls_sni_tcp"
    )


def _current_routes(
    state: StateDocument,
) -> tuple[dict[str, RelayCandidate], dict[str, Selection]]:
    records: dict[str, ManagedRecord] = {}
    selections: dict[str, Selection] = {}
    for record in state.managed_records:
        if record.domain in records:
            raise SafetyBlocked("Duplicate owned domain")
        records[record.domain] = record
    for cached in state.selections:
        if cached.domain in selections:
            raise SafetyBlocked("Duplicate route selection")
        selections[cached.domain] = cached
    current: dict[str, RelayCandidate] = {}
    for domain, record in records.items():
        selection = selections.get(domain)
        if selection is not None:
            candidate = selection.candidate
            if (
                candidate.domain != domain
                or candidate.ip != record.content
                or candidate.provider_id != record.provider_id
            ):
                raise SafetyBlocked("Selection disagrees with owned record")
        else:
            candidate = RelayCandidate(
                record.provider_id,
                domain,
                record.content,
                443,
                "tls_sni_tcp",
                0,
                "managed-state",
                None,
            )
        if not _valid_endpoint(candidate, domain):
            raise SafetyBlocked("Owned route has an invalid endpoint")
        current[domain] = candidate
    return current, selections


def _probe_statuses(
    probes: tuple[ProbeResult, ...],
    now: datetime,
) -> dict[tuple[str, str, str, int, str], ProbeStatus]:
    grouped: dict[tuple[str, str, str, int, str], set[ProbeStatus]] = {}
    for result in probes:
        # now is the captured run start, not the end of a batch.
        if result.checked_at.tzinfo is None or not (
            now <= result.checked_at <= now + timedelta(minutes=20)
        ):
            continue
        grouped.setdefault(endpoint_key(result.candidate), set()).add(result.status)
    return {
        key: next(iter(statuses)) if len(statuses) == 1 else "unknown"
        for key, statuses in grouped.items()
    }


def select_routes(
    catalog: ServiceCatalog,
    snapshots: tuple[RelaySnapshot, ...],
    probes: tuple[ProbeResult, ...],
    state: StateDocument,
    now: datetime,
) -> tuple[RouteDecision, ...]:
    """Retain owned routes on uncertainty; select only fresh, verified endpoints."""
    if now.tzinfo is None:
        raise SafetyBlocked("Routing requires an aware run timestamp")
    current, selections = _current_routes(state)
    eligible = tuple(
        snapshot
        for snapshot in snapshots
        if all(c.provider_id == snapshot.provider_id for c in snapshot.candidates)
    )
    fresh = tuple(
        snapshot
        for snapshot in eligible
        if snapshot.fetched_at.tzinfo is not None
        and snapshot.fetched_at == now
        and not any(warning.startswith("stale:") for warning in snapshot.warnings)
    )
    pools = candidate_pools(fresh, catalog)
    observed = candidate_pools(eligible, catalog)
    statuses = _probe_statuses(probes, now)
    domains = set(current) | set(observed)
    for policy in catalog.services:
        if policy.enabled:
            domains.update(policy.required_domains)
    decisions: list[RouteDecision] = []
    for domain in sorted(domains):
        service = catalog.select(domain)
        previous = current.get(domain)
        if service is None:
            if previous is not None:
                decisions.append(
                    RouteDecision(
                        domain,
                        previous,
                        "remove",
                        "catalog scope explicitly disabled or excluded",
                    )
                )
            continue
        selected = selections.get(domain)
        policy_changed = previous is not None and (
            selected is None or selected.routing_policy_digest != routing_policy_digest(service)
        )
        missing_required = any(required not in pools for required in service.required_domains)
        pool = pools.get(domain, ())
        previous_present = previous is not None and any(
            endpoint_key(c) == endpoint_key(previous) for c in pool
        )
        status = statuses.get(endpoint_key(previous), "unknown") if previous else "unknown"
        healthy = next(
            (c for c in pool if statuses.get(endpoint_key(c)) == "tls_ok"),
            None,
        )
        if missing_required:
            reason = "degraded: required source domains missing or not fresh"
        elif previous is not None and not policy_changed and previous_present:
            if status == "tls_ok":
                decisions.append(
                    RouteDecision(domain, previous, "retain", "current route verified")
                )
                continue
            if status == "unknown":
                reason = "degraded: current route verification unknown"
            elif healthy is not None:
                decisions.append(
                    RouteDecision(domain, healthy, "select", "verified backup selected")
                )
                continue
            else:
                reason = "degraded: current route failed; no verified backup"
        elif healthy is not None:
            reason = (
                "policy changed; verified route selected"
                if policy_changed
                else "verified route selected"
            )
            decisions.append(RouteDecision(domain, healthy, "select", reason))
            continue
        else:
            kind = "required" if domain in service.required_domains else "optional"
            reason = f"degraded: {kind} domain has no fresh verified route"
        if policy_changed:
            reason += "; policy change has no verified replacement"
        decisions.append(
            RouteDecision(
                domain,
                previous,
                "retain" if previous else "unavailable",
                reason,
            )
        )
    return tuple(decisions)


def to_desired(decisions: tuple[RouteDecision, ...]) -> tuple[DesiredRewrite, ...]:
    """Project explicit selected/retained routes; absence never requests deletion."""
    desired: dict[str, DesiredRewrite] = {}
    seen: set[str] = set()
    for decision in decisions:
        if decision.domain in seen:
            raise SafetyBlocked("Duplicate route decision")
        seen.add(decision.domain)
        if decision.action not in {"select", "retain"}:
            continue
        candidate = decision.candidate
        if candidate is None or not _valid_endpoint(candidate, decision.domain):
            raise SafetyBlocked("Desired rewrite requires a valid IPv4 TLS route")
        desired[decision.domain] = DesiredRewrite(decision.domain, candidate.ip)
    return tuple(desired[domain] for domain in sorted(desired))
