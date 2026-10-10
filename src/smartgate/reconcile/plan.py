"""Pure owned-record diff, conflict checks and bounded publication policy."""

import hashlib
import ipaddress
import json
import math
from dataclasses import asdict

from smartgate.catalog import ServiceCatalog, normalize_domain, suffix_matches
from smartgate.errors import ConfigError, SafetyBlocked
from smartgate.models import (
    ChangePlan,
    DesiredRewrite,
    DnsRecord,
    Operation,
    PlanContext,
    StateDocument,
)


def related(left: str, right: str) -> bool:
    return suffix_matches(left, right) or suffix_matches(right, left)


def dns_digest(records: tuple[DnsRecord, ...]) -> str:
    payload = [
        (r.record_id, r.domain, r.content)
        for r in sorted(records, key=lambda r: (r.domain, r.record_id, r.content))
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def public_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        return (
            address.version == 4
            and address.is_global
            and not address.is_multicast
            and not address.is_reserved
        )
    except ValueError:
        return False


def validate_policy(policy: PlanContext) -> None:
    if (
        type(policy.max_records) is not int
        or not 1 <= policy.max_records <= 100
        or type(policy.max_mutations) is not int
        or not 1 <= policy.max_mutations <= 200
        or type(policy.max_change_ratio) not in {int, float}
        or not 0 <= policy.max_change_ratio <= 1
        or not math.isfinite(policy.max_change_ratio)
        or any(
            type(flag) is not bool
            for flag in (policy.allow_prune, policy.bootstrap, policy.subtree_capabilities)
        )
        or policy.created_at.tzinfo is None
        or not policy.config_digest
    ):
        raise SafetyBlocked("Invalid plan safety policy")


def build_plan(
    desired: tuple[DesiredRewrite, ...],
    actual: tuple[DnsRecord, ...],
    state: StateDocument,
    removals: frozenset[str],
    context: PlanContext,
) -> ChangePlan:
    validate_policy(context)
    errors: list[str] = []
    operations: list[Operation] = []
    current = {r.record_id: r for r in actual}
    owned = {r.domain: r for r in state.managed_records}
    targets = {r.domain: r for r in desired}
    if (
        len(current) != len(actual)
        or len(owned) != len(state.managed_records)
        or len(targets) != len(desired)
    ):
        errors.append("duplicate input identity")
    if len({r.record_id for r in state.managed_records}) != len(state.managed_records):
        errors.append("duplicate owned ID")
    try:
        if any(
            normalize_domain(r.domain) != r.domain or not r.record_id or not r.content
            for r in actual
        ):
            errors.append("invalid actual record")
        if any(normalize_domain(r.domain) != r.domain or not public_ipv4(r.ipv4) for r in desired):
            errors.append("invalid desired rewrite")
        if any(normalize_domain(domain) != domain for domain in removals):
            errors.append("invalid removal domain")
    except ConfigError:
        errors.append("invalid DNS hostname")
    if state.schema_version not in {1, 2}:
        errors.append("unsupported state version")
    if state.journal and state.journal.phase not in {"verified", "rolled_back"}:
        errors.append("pending journal requires recovery")
    if context.bootstrap and (actual or state.managed_records):
        errors.append("bootstrap requires empty profile and ownership")
    for desired_record in desired:
        try:
            ServiceCatalog(context.services).validate_publication(desired_record.domain)
        except ConfigError:
            errors.append("catalog publication scope or subtree acceptance rejected")
    if desired and not context.subtree_capabilities:
        errors.append("subtree compatibility not confirmed")
    if set(targets) & removals:
        errors.append("desired and removal conflict")
    if removals - set(owned):
        errors.append("removal is not owned")
    ids = {r.record_id for r in state.managed_records}
    for record in state.managed_records:
        if current.get(record.record_id) != DnsRecord(
            record.record_id, record.domain, record.content
        ):
            errors.append("owned record drift")
    domains = set(targets) | (removals if context.allow_prune else frozenset())
    if any(related(f.domain, d) for f in actual if f.record_id not in ids for d in domains):
        errors.append("foreign domain or subtree conflict")
    for domain, target in sorted(targets.items()):
        before = owned.get(domain)
        if before is None:
            operations.append(Operation("create", domain, None, target))
        elif before.content != target.ipv4:
            operations.append(
                Operation(
                    "replace", domain, DnsRecord(before.record_id, domain, before.content), target
                )
            )
    if context.allow_prune:
        for domain in sorted(removals - set(targets)):
            removed = owned.get(domain)
            if removed:
                operations.append(
                    Operation(
                        "delete",
                        domain,
                        DnsRecord(removed.record_id, domain, removed.content),
                        None,
                    )
                )
    operations.sort(key=lambda op: (op.domain, op.kind))
    creates = sum(op.kind == "create" for op in operations)
    deletes = sum(op.kind == "delete" for op in operations)
    changed = sum(op.kind in {"replace", "delete"} for op in operations)
    if len(desired) > context.max_records or len(owned) + creates - deletes > context.max_records:
        errors.append("managed record limit exceeded")
    if len(operations) > context.max_mutations:
        errors.append("mutation limit exceeded")
    if not context.bootstrap and changed / max(len(owned), 1) > context.max_change_ratio:
        errors.append("change ratio exceeded")
    digest = dns_digest(actual)
    identity = {
        "desired": [asdict(r) for r in sorted(desired, key=lambda r: r.domain)],
        "removals": sorted(removals),
        "operations": [asdict(op) for op in operations],
        "policy": asdict(context),
        "state_revision": state.revision,
        "dns_digest": digest,
        "blocked": sorted(set(errors)),
    }
    # Keep the persisted schema-v1 plan hash contract unchanged.
    if state.target_id or state.target_fingerprint:
        identity.update(target_id=state.target_id, target_fingerprint=state.target_fingerprint)
    identifier = hashlib.sha256(
        json.dumps(
            identity, sort_keys=True, default=lambda value: value.isoformat(), separators=(",", ":")
        ).encode()
    ).hexdigest()
    return ChangePlan(
        identifier,
        context.config_digest,
        digest,
        state.revision,
        context.created_at,
        tuple(operations),
        tuple(sorted(set(errors))),
        context,
        tuple(sorted(desired, key=lambda r: r.domain)),
        tuple(sorted(removals)),
        state.target_id,
        state.target_fingerprint,
    )
