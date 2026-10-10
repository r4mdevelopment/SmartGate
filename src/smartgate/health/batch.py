"""Deterministic candidate batches and cursor advancement without state mutation."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

from smartgate.candidates import candidate_pools, endpoint_key
from smartgate.catalog import ServiceCatalog
from smartgate.errors import SafetyBlocked
from smartgate.health.tls import validate_probe_policy
from smartgate.models import (
    ProbeCursor,
    ProbePolicy,
    ProbeResult,
    RelayCandidate,
    RelaySnapshot,
    RouteDecision,
    StateDocument,
)
from smartgate.ports import HealthChecker


def select_probe_batch(
    snapshots: tuple[RelaySnapshot, ...],
    state: StateDocument,
    catalog: ServiceCatalog,
    policy: ProbePolicy,
) -> tuple[RelayCandidate, ...]:
    validate_probe_policy(policy)
    pools = candidate_pools(snapshots, catalog)
    current = {
        s.domain: s.candidate for s in state.selections if catalog.select(s.domain) is not None
    }
    cursors = {c.domain: c.index for c in state.probe_cursors}
    selected: list[RelayCandidate] = []
    for domain in sorted(set(pools) | set(current)):
        pool = pools.get(domain, ())
        choices: list[RelayCandidate] = []
        owned = current.get(domain)
        if owned is not None:
            choices.append(next((c for c in pool if endpoint_key(c) == endpoint_key(owned)), owned))
        if pool:
            offset = cursors.get(domain, 0) % len(pool)
            for candidate in pool[offset:] + pool[:offset]:
                if not any(endpoint_key(c) == endpoint_key(candidate) for c in choices):
                    choices.append(candidate)
        available = policy.max_pairs - len(selected)
        selected.extend(choices[: min(policy.max_candidates_per_domain, available)])
        if len(selected) >= policy.max_pairs:
            break
    return tuple(selected)


def advance_probe_cursors(
    snapshots: tuple[RelaySnapshot, ...],
    state: StateDocument,
    catalog: ServiceCatalog,
    results: tuple[ProbeResult, ...],
) -> tuple[ProbeCursor, ...]:
    pools = candidate_pools(snapshots, catalog)
    cursors = {c.domain: c.index for c in state.probe_cursors}
    for domain, pool in pools.items():
        observed = [r for r in results if r.candidate.domain == domain]
        if not observed or any(r.status == "tls_ok" for r in observed):
            continue
        positions = {endpoint_key(c): index for index, c in enumerate(pool)}
        checked = [
            positions[endpoint_key(r.candidate)]
            for r in observed
            if endpoint_key(r.candidate) in positions
        ]
        if checked:
            cursors[domain] = (checked[-1] + 1) % len(pool)
    return tuple(ProbeCursor(domain, index) for domain, index in sorted(cursors.items()))


def probe_candidates(
    candidates: tuple[RelayCandidate, ...],
    checker: HealthChecker,
    policy: ProbePolicy,
    vantage: str,
) -> tuple[ProbeResult, ...]:
    validate_probe_policy(policy)
    if vantage not in {"local", "github_runner"}:
        raise SafetyBlocked("Unknown probe vantage")
    counts = Counter(c.domain for c in candidates)
    if len(candidates) > policy.max_pairs or any(
        count > policy.max_candidates_per_domain for count in counts.values()
    ):
        raise SafetyBlocked("Probe batch exceeds pair or domain budget")

    def check(candidate: RelayCandidate) -> ProbeResult:
        try:
            result = checker.probe(candidate, policy)
            if result.candidate != candidate:
                raise SafetyBlocked("Checker returned a different candidate")
            return replace(result, vantage=vantage)
        except Exception:
            return ProbeResult(
                candidate, "unknown", datetime.now(UTC), vantage, None, "unexpected probe failure"
            )

    with ThreadPoolExecutor(max_workers=policy.concurrency) as executor:
        return tuple(executor.map(check, candidates))


def select_confirmation_batch(
    results: tuple[ProbeResult, ...],
    state: StateDocument,
    decisions: tuple[RouteDecision, ...],
    policy: ProbePolicy,
) -> tuple[RelayCandidate, ...]:
    """Recheck transient failures of published routes within the remaining run budget."""
    validate_probe_policy(policy)
    remaining = max(0, policy.max_pairs - len(results))
    affected = {
        d.domain
        for d in decisions
        if d.reason
        in {
            "verified backup selected",
            "degraded: current route failed; no verified backup",
        }
    }
    owned = {r.domain: r for r in state.managed_records}
    selected: list[RelayCandidate] = []
    seen: set[tuple[str, str, str, int, str]] = set()
    for result in results:
        candidate = result.candidate
        record = owned.get(candidate.domain)
        key = endpoint_key(candidate)
        if (
            len(selected) < remaining
            and candidate.domain in affected
            and record is not None
            and candidate.provider_id == record.provider_id
            and candidate.ip == record.content
            and result.status == "failed"
            and result.reason in {"connection timed out", "TLS connection failed"}
            and key not in seen
        ):
            selected.append(candidate)
            seen.add(key)
    return tuple(selected)
