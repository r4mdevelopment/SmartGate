import threading
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from smartgate.catalog import parse_catalog
from smartgate.errors import SafetyBlocked
from smartgate.health.batch import advance_probe_cursors, probe_candidates, select_probe_batch
from smartgate.models import (
    ProbeCursor,
    ProbePolicy,
    ProbeResult,
    RelayCandidate,
    RelaySnapshot,
    Selection,
    StateDocument,
)

NOW = datetime(2026, 10, 7, tzinfo=UTC)
CATALOG = parse_catalog(
    {"chatgpt": {"include_suffixes": ["chatgpt.com"], "provider_order": ["first", "second"]}}
)
STATE = StateDocument(1, "abcdef", None)


def candidate(ip, domain="chatgpt.com", provider="first"):
    return RelayCandidate(provider, domain, ip, 443, "tls_sni_tcp", 0, "fixture", "RU")


def snapshot(candidates, provider="first"):
    return RelaySnapshot(provider, NOW, "digest", tuple(candidates), ())


def result(c, status="failed"):
    return ProbeResult(c, status, NOW, "local", None, "fixture")


def test_current_is_first_and_other_candidates_use_numeric_ip_order():
    pool = [candidate("9.9.9.9"), candidate("8.8.8.8"), candidate("1.1.1.1")]
    state = replace(STATE, selections=(Selection("chatgpt.com", pool[0], "policy"),))
    batch = select_probe_batch((snapshot(pool),), state, CATALOG, ProbePolicy())
    assert batch == (pool[0], pool[2])


def test_provider_order_is_preserved_before_ip_order():
    first = candidate("9.9.9.9")
    second = candidate("1.1.1.1", provider="second")
    batch = select_probe_batch(
        (snapshot([second], "second"), snapshot([first])), STATE, CATALOG, ProbePolicy()
    )
    assert batch == (first, second)


def test_failed_candidates_advance_cursor_without_losing_the_pool():
    pool = [candidate("1.1.1.1"), candidate("8.8.8.8"), candidate("9.9.9.9")]
    sources = (snapshot(pool),)
    batch = select_probe_batch(sources, STATE, CATALOG, ProbePolicy())
    assert batch == tuple(pool[:2])
    cursors = advance_probe_cursors(sources, STATE, CATALOG, tuple(result(c) for c in batch))
    following = replace(STATE, probe_cursors=cursors)
    assert select_probe_batch(sources, following, CATALOG, ProbePolicy())[0] == pool[2]


def test_cursor_advances_after_failed_current_and_backup():
    pool = [candidate("1.1.1.1"), candidate("8.8.8.8"), candidate("9.9.9.9")]
    state = replace(STATE, selections=(Selection("chatgpt.com", pool[0], "policy"),))
    sources = (snapshot(pool),)
    batch = select_probe_batch(sources, state, CATALOG, ProbePolicy())
    cursors = advance_probe_cursors(sources, state, CATALOG, tuple(result(c) for c in batch))
    next_state = replace(state, probe_cursors=cursors)
    assert select_probe_batch(sources, next_state, CATALOG, ProbePolicy()) == (pool[0], pool[2])


def test_unprobed_or_successful_domains_do_not_advance_cursor():
    pool = [candidate("1.1.1.1"), candidate("8.8.8.8")]
    state = replace(STATE, probe_cursors=(ProbeCursor("chatgpt.com", 1),))
    sources = (snapshot(pool),)
    assert advance_probe_cursors(sources, state, CATALOG, ()) == state.probe_cursors
    assert (
        advance_probe_cursors(sources, state, CATALOG, (result(pool[1], "tls_ok"),))
        == state.probe_cursors
    )


def test_pair_domain_and_ipv4_limits_are_applied_before_probe():
    pool = [
        candidate(ip, f"d{domain}.chatgpt.com")
        for domain in range(110)
        for ip in ["1.1.1.1", "8.8.8.8", "9.9.9.9", "2606:4700::1111"]
    ]
    batch = select_probe_batch((snapshot(pool),), STATE, CATALOG, ProbePolicy())
    assert len(batch) == 200
    assert max(Counter(c.domain for c in batch).values()) == 2
    assert not any(":" in c.ip for c in batch)


def test_concurrency_and_result_order_and_vantage_are_explicit():
    pool = tuple(candidate("8.8.8.8", f"d{i}.chatgpt.com") for i in range(20))
    lock = threading.Lock()
    barrier = threading.Barrier(4)
    active = [0, 0]
    calls = [0]

    class Checker:
        def probe(self, c, policy):
            with lock:
                active[0] += 1
                active[1] = max(active)
                calls[0] += 1
                number = calls[0]
            if number <= 4:
                barrier.wait(timeout=3)
            time.sleep(0.002)
            with lock:
                active[0] -= 1
            return result(c, "tls_ok")

    outcomes = probe_candidates(pool, Checker(), ProbePolicy(concurrency=4), "github_runner")
    assert active[1] == 4
    assert tuple(r.candidate for r in outcomes) == pool
    assert all(r.vantage == "github_runner" for r in outcomes)


def test_batch_converts_checker_exception_to_safe_unknown():
    class Checker:
        def probe(self, c, policy):
            raise RuntimeError("secret")

    outcomes = probe_candidates((candidate("8.8.8.8"),), Checker(), ProbePolicy(), "local")
    assert outcomes[0].status == "unknown" and "secret" not in outcomes[0].reason


@pytest.mark.parametrize(
    "policy",
    [
        ProbePolicy(concurrency=9),
        ProbePolicy(max_pairs=201),
        ProbePolicy(max_candidates_per_domain=3),
        ProbePolicy(attempts=3),
        ProbePolicy(timeout_s=5),
    ],
)
def test_out_of_budget_policy_is_blocked(policy):
    with pytest.raises(SafetyBlocked):
        probe_candidates((), object(), policy, "local")


def test_invalid_large_timeout_is_blocked_without_numeric_overflow():
    with pytest.raises(SafetyBlocked):
        probe_candidates((), object(), ProbePolicy(timeout_s=10**1000), "local")


def test_batch_rejects_oversized_input_before_calling_checker():
    pool = tuple(candidate("8.8.8.8", f"d{i}.chatgpt.com") for i in range(201))
    with pytest.raises(SafetyBlocked):
        probe_candidates(pool, object(), ProbePolicy(), "local")


def test_batch_rejects_three_candidates_for_one_domain():
    pool = tuple(candidate(ip) for ip in ["1.1.1.1", "8.8.8.8", "9.9.9.9"])
    with pytest.raises(SafetyBlocked):
        probe_candidates(pool, object(), ProbePolicy(), "local")
