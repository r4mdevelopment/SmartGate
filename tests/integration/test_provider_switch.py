from dataclasses import replace
from datetime import UTC, datetime

from smartgate.catalog import parse_catalog
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


def catalog(order):
    return parse_catalog(
        {
            "chatgpt": {
                "include_suffixes": ["chatgpt.com"],
                "required_domains": ["chatgpt.com"],
                "provider_order": order,
            }
        }
    )


def test_provider_switch_changes_policy_and_desired_without_dns_dependencies():
    original, migrated = catalog(["gh", "own"]), catalog(["own", "gh"])
    gh = RelayCandidate("gh", "chatgpt.com", "8.8.8.8", 443, "tls_sni_tcp", 0, "hosts", "RU")
    own = replace(gh, provider_id="own", ip="1.1.1.1", source_id="manifest", source_region=None)
    state = StateDocument(
        1,
        "abcdef",
        None,
        managed_records=(ManagedRecord("owned-id", gh.domain, gh.ip, gh.provider_id, NOW),),
        selections=(Selection(gh.domain, gh, routing_policy_digest(original.services[0])),),
    )
    sources = tuple(RelaySnapshot(c.provider_id, NOW, "digest", (c,), ()) for c in [gh, own])
    probes = tuple(ProbeResult(c, "tls_ok", NOW, "local", 50, "verified") for c in [gh, own])
    assert routing_policy_digest(original.services[0]) != routing_policy_digest(
        migrated.services[0]
    )
    before = select_routes(original, sources, probes, state, NOW)
    after = select_routes(migrated, sources, probes, state, NOW)
    assert before[0].action == "retain" and to_desired(before)[0].ipv4 == "8.8.8.8"
    assert after[0].action == "select" and to_desired(after)[0].ipv4 == "1.1.1.1"
    assert type(to_desired(before)[0]) is type(to_desired(after)[0])


def test_disabled_old_provider_without_healthy_replacement_is_retained_degraded():
    old, new = catalog(["gh", "own"]), catalog(["own"])
    gh = RelayCandidate("gh", "chatgpt.com", "8.8.8.8", 443, "tls_sni_tcp", 0, "hosts", "RU")
    own = replace(gh, provider_id="own", ip="1.1.1.1")
    state = StateDocument(
        1,
        "abcdef",
        None,
        managed_records=(ManagedRecord("owned-id", gh.domain, gh.ip, gh.provider_id, NOW),),
        selections=(Selection(gh.domain, gh, routing_policy_digest(old.services[0])),),
    )
    sources = (RelaySnapshot("own", NOW, "digest", (own,), ()),)
    probes = (ProbeResult(own, "failed", NOW, "local", None, "timeout"),)
    decisions = select_routes(new, sources, probes, state, NOW)
    assert decisions[0].action == "retain" and decisions[0].candidate == gh
    assert "degraded" in decisions[0].reason and "policy" in decisions[0].reason


def test_real_relay_adapters_switch_and_return_with_same_dns_ownership():
    from pathlib import Path

    import httpx

    from smartgate.app import AppDependencies, run
    from smartgate.config import load_config
    from smartgate.models import ProviderConfig
    from smartgate.providers.geohide import SourceClient
    from smartgate.providers.registry import create_relay_provider
    from tests.integration.test_app import Checker
    from tests.reconcile_fakes import NOW as APP_NOW
    from tests.reconcile_fakes import FakeDns, MemoryStore, verify_ok

    source_text = Path("tests/fixtures/hosts/geohide-chatgpt.hosts").read_text(encoding="utf-8")
    source = SourceClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=source_text))
    )
    try:
        config = load_config(Path("config/smartgate.example.toml"), {})
        geohide = config.providers[0]
        own = ProviderConfig(
            "own",
            "selfhosted",
            manifest_path=Path("tests/fixtures/manifest/geohide-equivalent.json").resolve(),
        )
        service = replace(
            config.catalog.services[0],
            include_suffixes=("chatgpt.com",),
            provider_order=(geohide.id, own.id),
        )
        config = replace(
            config,
            providers=(geohide, own),
            catalog=replace(config.catalog, services=(service,)),
            sync=replace(config.sync, enabled=True),
        )
        deps = AppDependencies(
            {p.id: create_relay_provider(p, source) for p in config.providers},
            Checker(),
            FakeDns(),
            MemoryStore(),
            verify_ok,
            lambda: APP_NOW,
            "abcdef",
            bootstrap=True,
            subtree_capabilities=True,
        )
        assert run(config, "sync", True, deps).status == "applied"
        original_id = deps.store.state.managed_records[0].record_id
        deps = replace(deps, bootstrap=False)
        config = replace(
            config,
            catalog=replace(
                config.catalog, services=(replace(service, provider_order=(own.id, geohide.id)),)
            ),
        )
        assert run(config, "sync", True, deps).status == "no_change"
        assert deps.store.state.managed_records[0].provider_id == "own"
        assert deps.store.state.managed_records[0].record_id == original_id
        config = replace(config, catalog=replace(config.catalog, services=(service,)))
        assert run(config, "sync", True, deps).status == "no_change"
        assert deps.store.state.managed_records[0].provider_id == geohide.id
        assert deps.dns.writes == 1
    finally:
        source.close()
