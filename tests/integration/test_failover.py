from collections import Counter
from dataclasses import replace
from importlib import import_module

import pytest

from smartgate.app import run
from smartgate.catalog import ServiceCatalog
from smartgate.dns.types import DnsTarget
from smartgate.models import DnsRecord, ProbeResult, RelaySnapshot
from tests.integration.test_app import setup
from tests.reconcile_fakes import NOW, target

TARGET = DnsTarget(
    "primary", "nextdns", "https://api.nextdns.io", "abcdef", "https://dns.nextdns.io/abcdef"
)


def module():
    return import_module("smartgate.failover")


class Relay:
    def __init__(self, domains, stale=False, broken=False):
        self.domains, self.stale, self.broken = domains, stale, broken

    def fetch(self, provider, now):
        if self.broken:
            raise RuntimeError("private source details")
        candidates = tuple(
            replace(target(d, ip, provider.id).candidate, priority=priority)
            for d in self.domains
            for ip, priority in [("8.8.8.8", 0), ("1.1.1.1", 1)]
        )
        return RelaySnapshot(
            provider.id, now, "digest", candidates, ("stale: test",) if self.stale else ()
        )


class Checker:
    def __init__(self, failed=(), recover=False, backup_failed=False, unknown=False):
        self.failed, self.recover, self.backup_failed, self.unknown = (
            set(failed),
            recover,
            backup_failed,
            unknown,
        )
        self.calls = Counter()

    def probe(self, candidate, policy):
        key = (candidate.domain, candidate.ip)
        self.calls[key] += 1
        fails = candidate.domain in self.failed and candidate.ip == "8.8.8.8"
        if fails and self.recover and self.calls[key] > 1:
            fails = False
        fails = fails or self.backup_failed and candidate.ip == "1.1.1.1"
        status = "unknown" if fails and self.unknown else "failed" if fails else "tls_ok"
        return ProbeResult(
            candidate,
            status,
            NOW,
            "local",
            1.0,
            "connection timed out" if fails else "verified TLS handshake",
        )


def prepared(monkeypatch, count=25, failed=None, **kwargs):
    config, deps = setup()
    domains = ("chatgpt.com",) + tuple(f"d{i}.chatgpt.com" for i in range(count - 1))
    provider = config.providers[0]
    service = replace(
        config.catalog.services[0],
        include_suffixes=("chatgpt.com",),
        required_domains=("chatgpt.com",),
        provider_order=(provider.id,),
    )
    config = replace(
        config,
        providers=(provider,),
        catalog=ServiceCatalog((service,)),
        sync=replace(config.sync, max_mutations=100),
    )
    deps = replace(
        deps,
        relays={provider.id: Relay(domains)},
        checker=Checker(),
        dns_target=TARGET,
        profile_id="primary",
    )
    assert run(config, "sync", True, deps).status == "applied"
    config = replace(config, sync=replace(config.sync, max_mutations=20, max_change_ratio=0.25))
    deps = replace(
        deps,
        bootstrap=False,
        checker=Checker(domains if failed is None else domains[:failed], **kwargs),
    )
    delays = []
    monkeypatch.setattr(module(), "sleep", delays.append)
    return config, deps, domains, delays


def observe(config, deps, apply=True):
    return module().run_failover(config, apply, deps)


def test_mass_failover_replaces_25_with_normal_limits_preserved(monkeypatch):
    config, deps, domains, delays = prepared(monkeypatch)
    result = observe(config, deps)
    assert result.status == "applied" and len(result.plan.operations) == 25
    assert result.journal_phase == "verified" and len(result.checks) == 100
    assert all(r.content == "1.1.1.1" for r in deps.dns.records)
    assert config.sync.max_mutations == 20 and config.sync.max_change_ratio == 0.25
    assert delays == [5.0]
    assert all(deps.checker.calls[(d, "8.8.8.8")] == 2 for d in domains)


@pytest.mark.parametrize("count,failed", [(4, 4), (10, 4), (11, 5)])
def test_subthreshold_failures_make_no_dns_or_state_writes(monkeypatch, count, failed):
    config, deps, _, _ = prepared(monkeypatch, count, failed)
    state, records, saves, writes = (
        deps.store.state,
        tuple(deps.dns.records),
        deps.store.saves,
        deps.dns.writes,
    )
    assert observe(config, deps).status == "no_change"
    assert deps.store.state == state and tuple(deps.dns.records) == records
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_exact_half_only_replaces_failed_routes_and_preserves_healthy_ids(monkeypatch):
    config, deps, domains, _ = prepared(monkeypatch, 10, 5)
    healthy = tuple(r for r in deps.dns.records if r.domain in domains[5:])
    result = observe(config, deps)
    assert result.status == "applied" and len(result.plan.operations) == 5
    assert all(r in deps.dns.records for r in healthy)


def test_temporary_mass_failure_recovers_without_writes(monkeypatch):
    config, deps, _, delays = prepared(monkeypatch, recover=True)
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "no_change" and delays == [5.0]
    assert deps.store.saves == saves and deps.dns.writes == writes


@pytest.mark.parametrize("kwargs", [{"backup_failed": True}, {"unknown": True}])
def test_no_verified_backup_or_unknown_results_never_write(monkeypatch, kwargs):
    config, deps, _, _ = prepared(monkeypatch, **kwargs)
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status in {"blocked", "no_change"}
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_more_than_25_confirmed_failures_stops_before_any_change(monkeypatch):
    config, deps, _, _ = prepared(monkeypatch, 26)
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "blocked"
    assert deps.store.saves == saves and deps.dns.writes == writes


@pytest.mark.parametrize("stale,broken", [(True, False), (False, True)])
def test_stale_or_unavailable_sources_do_not_supply_backup(monkeypatch, stale, broken):
    config, deps, domains, _ = prepared(monkeypatch)
    deps = replace(deps, relays={config.providers[0].id: Relay(domains, stale, broken)})
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "blocked"
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_preview_is_read_only_even_with_mass_failure(monkeypatch):
    config, deps, _, _ = prepared(monkeypatch)
    saves, writes = deps.store.saves, deps.dns.writes
    result = observe(config, deps, False)
    assert result.status == "preview" and len(result.plan.operations) == 25
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_foreign_record_is_not_probed_or_modified(monkeypatch):
    config, deps, _, _ = prepared(monkeypatch)
    foreign = DnsRecord("foreign", "other.example.org", "9.9.9.9")
    deps.dns.records.append(foreign)
    assert observe(config, deps).status == "applied"
    assert foreign in deps.dns.records and not any(
        key[0] == foreign.domain for key in deps.checker.calls
    )


@pytest.mark.parametrize(
    "kind", ["pending", "drift", "binding", "disabled", "no_change", "offline", "bootstrap"]
)
def test_unsafe_context_blocks_before_writes(monkeypatch, kind):
    config, deps, _, _ = prepared(monkeypatch)
    if kind == "pending":
        deps.store.state = replace(
            deps.store.state, journal=replace(deps.store.state.journal, phase="partial")
        )
    elif kind == "drift":
        deps.dns.records[0] = replace(deps.dns.records[0], content="9.9.9.9")
    elif kind == "binding":
        deps.store.state = replace(deps.store.state, target_fingerprint="f" * 64)
    elif kind == "disabled":
        config = replace(config, sync=replace(config.sync, enabled=False))
    else:
        deps = replace(
            deps,
            **{
                "no_change": {"require_no_change": True},
                "offline": {"offline": True},
                "bootstrap": {"bootstrap": True},
            }[kind],
        )
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "blocked"
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_dns_change_during_confirmation_blocks_without_state_write(monkeypatch):
    config, deps, _, _ = prepared(monkeypatch)

    def change(delay):
        deps.dns.records[0] = replace(deps.dns.records[0], content="9.9.9.9")

    monkeypatch.setattr(module(), "sleep", change)
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "blocked"
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_interruption_keeps_durable_journal_for_existing_recovery(monkeypatch):
    config, deps, _, _ = prepared(monkeypatch)
    deps.dns.mode = "kill_after_delete"
    with pytest.raises(KeyboardInterrupt):
        observe(config, deps)
    assert deps.store.state.journal.phase not in {"verified", "rolled_back"}
    assert len(deps.store.state.journal.plan.operations) == 25


def test_real_fetch_duration_does_not_turn_fresh_source_into_stale_cache(monkeypatch):
    from datetime import timedelta

    from tests.reconcile_fakes import verify_ok

    config, deps, domains, _ = prepared(monkeypatch)
    now = [NOW]

    class AdvancingRelay(Relay):
        def fetch(self, provider, started):
            snapshot = super().fetch(provider, started)
            now[0] += timedelta(seconds=1)
            return snapshot

    deps = replace(
        deps,
        clock=lambda: now[0],
        relays={config.providers[0].id: AdvancingRelay(domains)},
        verifier=lambda p, d: tuple(replace(c, checked_at=now[0]) for c in verify_ok(p, d)),
    )
    original = deps.checker.probe
    deps.checker.probe = lambda c, p: replace(original(c, p), checked_at=now[0])
    assert observe(config, deps).status == "applied"


def test_failed_second_backup_confirmation_never_writes(monkeypatch):
    config, deps, _, _ = prepared(monkeypatch)
    original = deps.checker.probe

    def check(candidate, policy):
        result = original(candidate, policy)
        if candidate.ip == "1.1.1.1" and deps.checker.calls[(candidate.domain, candidate.ip)] > 1:
            return replace(result, status="failed", reason="connection timed out")
        return result

    deps.checker.probe = check
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "blocked"
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_insufficient_probe_budget_never_raises_limits_or_writes(monkeypatch):
    config, deps, _, _ = prepared(monkeypatch)
    config = replace(config, probe=replace(config.probe, max_pairs=75))
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "blocked"
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_wrong_profile_dns_answers_block_before_failover(monkeypatch):
    from tests.reconcile_fakes import verify_ok

    config, deps, _, _ = prepared(monkeypatch)
    deps = replace(deps, verifier=lambda p, d: tuple(replace(c, ok=False) for c in verify_ok(p, d)))
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "blocked"
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_app_emergency_mode_cannot_fall_through_to_normal_publication(monkeypatch):
    config, deps, domains, _ = prepared(monkeypatch)
    deps = replace(deps, emergency_only=True)
    assert run(config, "preview", False, deps).status == "preview"
    result = run(config, "sync", True, deps)
    assert result.status == "applied" and len(result.plan.operations) == len(domains)
    saves, writes = deps.store.saves, deps.dns.writes
    assert run(config, "sync", True, deps).status == "no_change"
    assert deps.store.saves == saves and deps.dns.writes == writes


@pytest.mark.parametrize("kind", ["policy", "selection", "state", "api", "budget"])
def test_conflict_at_late_backup_or_budget_gate_never_writes(monkeypatch, kind):
    from smartgate.errors import SafetyBlocked

    config, deps, _, _ = prepared(monkeypatch)
    if kind == "policy":
        config = replace(
            config,
            catalog=ServiceCatalog((replace(config.catalog.services[0], provider_order=()),)),
        )
    elif kind == "selection":
        deps.store.state = replace(deps.store.state, selections=())
    elif kind == "budget":

        def refuse(count):
            assert count == 25
            raise SafetyBlocked("Git request budget exhausted")

        deps = replace(deps, preflight_writes=refuse)
    else:
        original = deps.checker.probe

        def check(candidate, policy):
            result = original(candidate, policy)
            if (
                candidate.ip == "1.1.1.1"
                and deps.checker.calls[(candidate.domain, candidate.ip)] > 1
            ):
                if kind == "state":
                    deps.store.state = replace(deps.store.state, revision="concurrent")
                else:
                    deps.dns.records[0] = replace(deps.dns.records[0], content="9.9.9.9")
            return result

        deps.checker.probe = check
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "blocked"
    assert deps.store.saves == saves and deps.dns.writes == writes


def test_public_failover_summary_has_counts_without_route_details(monkeypatch):
    from smartgate.private_report import public_report

    config, deps, _, _ = prepared(monkeypatch)
    result = observe(config, deps, False)
    encoded, summary = public_report(result)
    assert '"failover_failed_confirmed": 25' in encoded and "Emergency-only" in summary
    for private in ("chatgpt.com", "8.8.8.8", "1.1.1.1", "abcdef"):
        assert private not in encoded + summary


@pytest.mark.parametrize("timestamp", ["stale", "future", "naive"])
@pytest.mark.parametrize("stage", ["initial", "confirmed", "backups", "backup_confirmed"])
def test_every_probe_batch_requires_fresh_aware_observations(monkeypatch, timestamp, stage):
    from datetime import timedelta

    config, deps, _, _ = prepared(monkeypatch)
    original = deps.checker.probe

    def check(candidate, policy):
        result = original(candidate, policy)
        occurrence = deps.checker.calls[(candidate.domain, candidate.ip)]
        current_stage = (
            ("initial" if occurrence == 1 else "confirmed")
            if candidate.ip == "8.8.8.8"
            else ("backups" if occurrence == 1 else "backup_confirmed")
        )
        if current_stage == stage:
            value = (
                NOW - timedelta(days=1)
                if timestamp == "stale"
                else NOW + timedelta(days=1)
                if timestamp == "future"
                else NOW.replace(tzinfo=None)
            )
            result = replace(result, checked_at=value)
        return result

    deps.checker.probe = check
    saves, writes = deps.store.saves, deps.dns.writes
    assert observe(config, deps).status == "blocked"
    assert deps.store.saves == saves and deps.dns.writes == writes
