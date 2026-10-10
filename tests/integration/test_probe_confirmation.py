from collections import Counter
from dataclasses import replace

import pytest

from smartgate.app import run
from smartgate.catalog import ServiceCatalog
from smartgate.models import ProbeResult, RelaySnapshot
from smartgate.report import workflow_exit_code
from tests.integration.test_app import setup
from tests.reconcile_fakes import NOW, target


class PairRelay:
    def fetch(self, provider, now):
        current = target("chatgpt.com", "8.8.8.8", provider.id).candidate
        backup = replace(current, ip="1.1.1.1", priority=1)
        return RelaySnapshot(provider.id, now, "digest", (current, backup), ())


class RecoveringChecker:
    def __init__(self, recovery="tls_ok", reason="connection timed out"):
        self.calls = Counter()
        self.recovery, self.reason = recovery, reason

    def probe(self, candidate, policy):
        self.calls[candidate.ip] += 1
        status = "tls_ok"
        reason = "verified TLS handshake"
        if candidate.ip == "8.8.8.8":
            status = "failed" if self.calls[candidate.ip] == 1 else self.recovery
            reason = (
                self.reason
                if status == "failed"
                else "unexpected probe failure"
                if status == "unknown"
                else reason
            )
        return ProbeResult(candidate, status, NOW, "github_runner", 1.0, reason)


def prepared(monkeypatch):
    config, deps = setup()
    config = replace(
        config,
        providers=config.providers[:1],
        catalog=ServiceCatalog(
            (replace(config.catalog.services[0], provider_order=(config.providers[0].id,)),)
        ),
    )
    deps = replace(deps, relays={config.providers[0].id: PairRelay()}, vantage="github_runner")
    assert run(config, "sync", True, deps).status == "applied"
    before = tuple(deps.dns.records)
    delays = []
    monkeypatch.setattr("smartgate.app.sleep", delays.append, raising=False)
    checker = RecoveringChecker()
    deps = replace(deps, checker=checker, bootstrap=False)
    return config, deps, checker, delays, before


@pytest.mark.parametrize("mode,apply", [("doctor", False), ("sync", True), ("preview", False)])
def test_delayed_confirmation_recovers_current_route_without_dns_changes(monkeypatch, mode, apply):
    config, deps, checker, delays, before = prepared(monkeypatch)
    writes, saves = deps.dns.writes, deps.store.saves
    report = run(config, mode, apply, deps)
    assert report.plan.operations == ()
    assert report.decisions[0].action == "retain"
    assert report.decisions[0].reason == "current route verified"
    assert workflow_exit_code(report) == 0
    assert delays == [5.0]
    assert checker.calls == {"8.8.8.8": 2, "1.1.1.1": 1}
    assert tuple(deps.dns.records) == before and deps.dns.writes == writes
    if not apply:
        assert deps.store.saves == saves
    assert len(report.probes) == 2
    assert all(r.status == "tls_ok" for r in report.probes)


def test_persistent_failure_stays_blocked_and_is_not_retried_indefinitely(monkeypatch):
    config, deps, checker, delays, before = prepared(monkeypatch)
    checker.recovery = "failed"
    writes, saves = deps.dns.writes, deps.store.saves
    report = run(config, "sync", True, deps)
    assert report.status == "blocked" and workflow_exit_code(report) != 0
    assert report.plan.blocked_reasons == ("change ratio exceeded",)
    assert delays == [5.0] and checker.calls["8.8.8.8"] == 2
    assert tuple(deps.dns.records) == before
    assert deps.dns.writes == writes and deps.store.saves == saves


def test_unknown_confirmation_never_returns_green(monkeypatch):
    config, deps, checker, delays, before = prepared(monkeypatch)
    checker.recovery = "unknown"
    report = run(config, "doctor", False, deps)
    assert workflow_exit_code(report) != 0
    assert report.decisions[0].reason == "degraded: current route verification unknown"
    assert delays == [5.0] and tuple(deps.dns.records) == before


@pytest.mark.parametrize("reason", ["certificate validation failed", "fixture failure"])
def test_non_network_failures_are_not_confirmed(monkeypatch, reason):
    config, deps, checker, delays, before = prepared(monkeypatch)
    checker.reason = reason
    report = run(config, "sync", True, deps)
    assert report.status == "blocked" and delays == []
    assert checker.calls["8.8.8.8"] == 1 and tuple(deps.dns.records) == before


def test_confirmation_respects_total_probe_budget(monkeypatch):
    config, deps, checker, delays, before = prepared(monkeypatch)
    config = replace(config, probe=replace(config.probe, max_pairs=2))
    report = run(config, "sync", True, deps)
    assert report.status == "blocked" and delays == []
    assert sum(checker.calls.values()) == 2 and tuple(deps.dns.records) == before


def test_healthy_current_route_has_no_extra_delay_or_probes(monkeypatch):
    config, deps, checker, delays, before = prepared(monkeypatch)
    checker.calls["8.8.8.8"] = 1
    report = run(config, "doctor", False, deps)
    assert workflow_exit_code(report) == 0 and delays == []
    assert sum(checker.calls.values()) == 3 and tuple(deps.dns.records) == before


@pytest.mark.parametrize("recovers", [True, False])
def test_all_25_current_routes_are_confirmed_before_mass_replacement(monkeypatch, recovers):
    config, deps, _, delays, _ = prepared(monkeypatch)
    domains = ("chatgpt.com",) + tuple(f"d{i}.chatgpt.com" for i in range(24))

    class ManyRelay:
        def fetch(self, provider, now):
            candidates = tuple(
                replace(target(d, ip, provider.id).candidate, priority=priority)
                for d in domains
                for ip, priority in [("8.8.8.8", 0), ("1.1.1.1", 1)]
            )
            return RelaySnapshot(provider.id, now, "digest", candidates, ())

    class ManyChecker:
        def __init__(self, fail=False):
            self.calls = Counter()
            self.fail = fail

        def probe(self, candidate, policy):
            key = (candidate.domain, candidate.ip)
            self.calls[key] += 1
            fails = (
                self.fail and candidate.ip == "8.8.8.8" and (self.calls[key] == 1 or not recovers)
            )
            return ProbeResult(
                candidate,
                "failed" if fails else "tls_ok",
                NOW,
                "github_runner",
                1.0,
                "connection timed out" if fails else "verified TLS handshake",
            )

    # Build a separate fixture ledger, then restore the normal production limits.
    config = replace(config, sync=replace(config.sync, max_mutations=30))
    deps = replace(deps, relays={config.providers[0].id: ManyRelay()}, checker=ManyChecker())
    assert run(config, "sync", True, deps).status == "applied"
    assert len(deps.dns.records) == 25
    config = replace(config, sync=replace(config.sync, max_mutations=20))
    checker = ManyChecker(fail=True)
    deps = replace(deps, checker=checker)
    before, writes = tuple(deps.dns.records), deps.dns.writes
    report = run(config, "sync", True, deps)
    assert delays == [5.0] and sum(checker.calls.values()) == 75
    assert tuple(deps.dns.records) == before and deps.dns.writes == writes
    if recovers:
        assert report.plan.operations == () and workflow_exit_code(report) == 0
        assert all(d.action == "retain" for d in report.decisions)
        assert len(report.checks) == 100 and all(c.ok for c in report.checks)
    else:
        assert report.status == "blocked"
        assert set(report.plan.blocked_reasons) == {
            "mutation limit exceeded",
            "change ratio exceeded",
        }


def test_recovery_without_healthy_backup_is_also_automatic(monkeypatch):
    config, deps, checker, delays, before = prepared(monkeypatch)
    original = checker.probe

    def probe(candidate, policy):
        result = original(candidate, policy)
        if candidate.ip == "1.1.1.1":
            return replace(result, status="failed", reason="connection timed out")
        return result

    checker.probe = probe
    report = run(config, "doctor", False, deps)
    assert delays == [5.0] and workflow_exit_code(report) == 0
    assert report.plan.operations == () and tuple(deps.dns.records) == before


def test_dns_drift_during_confirmation_is_not_hidden(monkeypatch):
    config, deps, checker, delays, before = prepared(monkeypatch)

    def wait(seconds):
        delays.append(seconds)
        deps.dns.records[0] = replace(deps.dns.records[0], content="8.8.4.4")

    monkeypatch.setattr("smartgate.app.sleep", wait)
    report = run(config, "doctor", False, deps)
    assert report.status == "blocked" and workflow_exit_code(report) != 0
    assert report.errors == ("DNS records changed during read-only verification",)


def test_confirmation_does_not_repeat_dns_mutations(monkeypatch):
    config, deps, checker, delays, before = prepared(monkeypatch)
    config = replace(config, sync=replace(config.sync, max_change_ratio=1.0))
    checker.recovery = "failed"
    writes = deps.dns.writes
    report = run(config, "sync", True, deps)
    assert report.status == "applied" and delays == [5.0]
    assert len(report.plan.operations) == 1 and dict(report.counts)["mutations"] == 1
    assert deps.dns.writes == writes + 2  # One delete/create replacement by the existing adapter.
    assert deps.dns.records[0].content == "1.1.1.1"
