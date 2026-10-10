from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from smartgate.catalog import parse_catalog
from smartgate.errors import SafetyBlocked
from smartgate.models import (
    ManagedRecord,
    ProbeResult,
    RelayCandidate,
    RelaySnapshot,
    Selection,
    StateDocument,
)
from smartgate.routing import routing_policy_digest, select_routes, to_desired

NOW = datetime(2026, 10, 7, tzinfo=UTC)
CATALOG = parse_catalog(
    {
        "chatgpt": {
            "include_suffixes": ["chatgpt.com"],
            "required_domains": ["chatgpt.com"],
            "provider_order": ["gh", "own"],
        }
    }
)
EMPTY = StateDocument(1, "abcdef", None)


def candidate(ip="8.8.8.8", provider="gh", domain="chatgpt.com", priority=0):
    return RelayCandidate(provider, domain, ip, 443, "tls_sni_tcp", priority, "fixture", "RU")


def source(*values, age=0):
    return RelaySnapshot(
        values[0].provider_id, NOW - timedelta(hours=age), "digest", tuple(values), ()
    )


def probe(c, status="tls_ok", latency=100):
    return ProbeResult(c, status, NOW, "local", latency if status == "tls_ok" else None, "fixture")


def owned(c, catalog=CATALOG):
    return replace(
        EMPTY,
        managed_records=(ManagedRecord("owned-id", c.domain, c.ip, c.provider_id, NOW),),
        selections=(Selection(c.domain, c, routing_policy_digest(catalog.services[0])),),
    )


def test_healthy_current_is_retained_even_when_reserve_is_faster():
    current = candidate("8.8.8.8")
    faster = candidate("1.1.1.1")
    decisions = select_routes(
        CATALOG,
        (source(current, faster),),
        (probe(current, latency=200), probe(faster, latency=1)),
        owned(current),
        NOW,
    )
    assert decisions[0].candidate == current and decisions[0].action == "retain"
    assert not decisions[0].reason.startswith("degraded")
    assert to_desired(decisions)[0].ipv4 == current.ip


def test_new_routes_use_provider_priority_and_numeric_ip_not_latency():
    gh = candidate("9.9.9.9")
    later = candidate("1.1.1.1", "own")
    worse_priority = candidate("1.1.1.1", priority=1)
    best = candidate("8.8.8.8")
    sources = (source(gh, worse_priority, best), source(later))
    probes = tuple(probe(c) for c in [gh, later, worse_priority, best])
    expected = select_routes(CATALOG, sources, probes, EMPTY, NOW)
    assert expected[0].candidate == best and expected[0].action == "select"
    assert (
        select_routes(CATALOG, tuple(reversed(sources)), tuple(reversed(probes)), EMPTY, NOW)
        == expected
    )


def test_failed_current_uses_verified_backup():
    current = candidate()
    reserve = candidate("1.1.1.1")
    decisions = select_routes(
        CATALOG,
        (source(current, reserve),),
        (probe(current, "failed"), probe(reserve)),
        owned(current),
        NOW,
    )
    assert decisions[0].action == "select" and decisions[0].candidate == reserve


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_failed_or_unknown_pool_preserves_existing_owned_route(status):
    current = candidate()
    decisions = select_routes(
        CATALOG, (source(current),), (probe(current, status),), owned(current), NOW
    )
    assert decisions[0].action == "retain" and decisions[0].reason.startswith("degraded")
    assert to_desired(decisions)[0].ipv4 == current.ip


def test_unknown_current_does_not_authorize_replacement_by_latency_or_guess():
    current, reserve = candidate(), candidate("1.1.1.1")
    decisions = select_routes(
        CATALOG,
        (source(current, reserve),),
        (probe(current, "unknown"), probe(reserve)),
        owned(current),
        NOW,
    )
    assert decisions[0].action == "retain" and decisions[0].candidate == current


def test_new_unverified_domain_is_unavailable():
    c = candidate()
    decisions = select_routes(CATALOG, (source(c),), (probe(c, "failed"),), EMPTY, NOW)
    assert decisions[0].action == "unavailable" and decisions[0].reason.startswith("degraded")
    assert to_desired(decisions) == ()


@pytest.mark.parametrize("age", [2, 24, 25])
def test_stale_or_expired_sources_never_create_or_replace_routes(age):
    current = candidate()
    reserve = candidate("1.1.1.1")
    sources = (source(current, reserve, age=age),)
    probes = (probe(current, "failed"), probe(reserve))
    existing = select_routes(CATALOG, sources, probes, owned(current), NOW)
    assert existing[0].action == "retain" and existing[0].candidate == current
    new = select_routes(CATALOG, sources, probes, EMPTY, NOW)
    assert new[0].action == "unavailable" and to_desired(new) == ()


def test_missing_required_source_domain_blocks_new_optional_route():
    c = candidate(domain="files.chatgpt.com")
    decisions = select_routes(CATALOG, (source(c),), (probe(c),), EMPTY, NOW)
    assert {d.domain for d in decisions} == {"chatgpt.com", "files.chatgpt.com"}
    assert all(d.action == "unavailable" for d in decisions)
    assert all("required" in d.reason for d in decisions)


def test_optional_failure_is_distinguished_from_required_failure():
    root = candidate()
    optional = candidate(domain="files.chatgpt.com")
    decisions = select_routes(
        CATALOG, (source(root, optional),), (probe(root), probe(optional, "failed")), EMPTY, NOW
    )
    outcomes = {d.domain: d for d in decisions}
    assert outcomes[root.domain].action == "select"
    assert outcomes[optional.domain].action == "unavailable"
    assert "optional" in outcomes[optional.domain].reason
    failed_root = select_routes(CATALOG, (source(root),), (probe(root, "failed"),), EMPTY, NOW)
    assert "required" in failed_root[0].reason


def test_missing_sources_never_mean_removal():
    current = candidate()
    decisions = select_routes(CATALOG, (), (), owned(current), NOW)
    assert decisions[0].action == "retain" and decisions[0].reason.startswith("degraded")


def test_disabling_catalog_scope_marks_only_owned_route_for_explicit_removal():
    current = candidate()
    disabled = parse_catalog(
        {
            "chatgpt": {
                "enabled": False,
                "include_suffixes": ["chatgpt.com"],
                "provider_order": ["gh"],
            }
        }
    )
    decisions = select_routes(disabled, (), (), owned(current), NOW)
    assert decisions[0].action == "remove" and to_desired(decisions) == ()
    assert select_routes(disabled, (source(current),), (probe(current),), EMPTY, NOW) == ()


def test_selection_cache_without_ownership_does_not_preserve_foreign_route():
    current = candidate()
    state = replace(EMPTY, selections=(Selection(current.domain, current, "old"),))
    decisions = select_routes(CATALOG, (), (), state, NOW)
    assert all(d.action != "retain" for d in decisions)


def test_unverified_or_conflicting_probe_cannot_be_reused():
    c = candidate()
    old = replace(probe(c), checked_at=NOW - timedelta(days=1))
    assert select_routes(CATALOG, (source(c),), (old,), EMPTY, NOW)[0].action == "unavailable"
    conflicts = (probe(c), probe(c, "failed"))
    assert select_routes(CATALOG, (source(c),), conflicts, EMPTY, NOW)[0].action == "unavailable"


def test_duplicate_owned_domain_is_blocked():
    current = candidate()
    state = owned(current)
    with pytest.raises(SafetyBlocked):
        select_routes(
            CATALOG, (), (), replace(state, managed_records=state.managed_records * 2), NOW
        )


def test_owned_record_without_selection_is_preserved_on_source_failure():
    c = candidate()
    state = replace(owned(c), selections=())
    decision = select_routes(CATALOG, (), (), state, NOW)[0]
    assert decision.action == "retain" and decision.candidate.ip == c.ip
    assert to_desired((decision,))[0].ipv4 == c.ip


def test_selection_disagreeing_with_ownership_is_blocked():
    c = candidate()
    state = replace(owned(c), selections=(Selection(c.domain, candidate("1.1.1.1"), "old"),))
    with pytest.raises(SafetyBlocked):
        select_routes(CATALOG, (), (), state, NOW)


def test_stale_warning_blocks_new_selection_even_with_run_timestamp():
    c = candidate()
    snapshot = replace(source(c), warnings=("stale: retain existing routes only",))
    assert select_routes(CATALOG, (snapshot,), (probe(c),), EMPTY, NOW)[0].action == "unavailable"


def test_probe_after_run_start_is_accepted_but_outside_run_budget_is_ignored():
    c = candidate()
    completed = replace(probe(c), checked_at=NOW + timedelta(seconds=8))
    assert select_routes(CATALOG, (source(c),), (completed,), EMPTY, NOW)[0].action == "select"
    future = replace(probe(c), checked_at=NOW + timedelta(minutes=21))
    assert select_routes(CATALOG, (source(c),), (future,), EMPTY, NOW)[0].action == "unavailable"


def test_provider_mismatch_in_snapshot_cannot_authorize_new_route():
    c = candidate()
    snapshot = replace(source(c), provider_id="own")
    assert select_routes(CATALOG, (snapshot,), (probe(c),), EMPTY, NOW)[0].action == "unavailable"


def test_routing_never_synthesizes_unknown_subdomains():
    c = candidate()
    assert {d.domain for d in select_routes(CATALOG, (source(c),), (probe(c),), EMPTY, NOW)} == {
        c.domain
    }


def test_policy_digest_ignores_set_order_but_preserves_provider_order():
    service = CATALOG.services[0]
    a = replace(service, include_suffixes=("chatgpt.com", "openai.com"))
    b = replace(service, include_suffixes=tuple(reversed(a.include_suffixes)))
    assert routing_policy_digest(a) == routing_policy_digest(b)
    assert routing_policy_digest(a) != routing_policy_digest(
        replace(a, provider_order=("own", "gh"))
    )


def test_invalid_or_duplicate_desired_route_is_blocked():
    from smartgate.models import RouteDecision

    bad = RouteDecision("chatgpt.com", candidate("127.0.0.1"), "select", "fixture")
    with pytest.raises(SafetyBlocked):
        to_desired((bad,))
    valid = replace(bad, candidate=candidate())
    with pytest.raises(SafetyBlocked):
        to_desired((valid, valid))
