from contextlib import ExitStack

import pytest

from smartgate.dns.registry import create_dns_bundle
from smartgate.errors import ConfigError
from smartgate.models import DnsConfig


def test_unknown_provider_fails_before_client_construction():
    with ExitStack() as stack, pytest.raises(ConfigError):
        create_dns_bundle(DnsConfig(provider="unknown"), {}, stack)


def test_nextdns_bundle_binds_api_and_verification_to_same_profile():
    with ExitStack() as stack:
        bundle = create_dns_bundle(
            DnsConfig(), {"NEXTDNS_PROFILE_ID": "abcdef", "NEXTDNS_API_KEY": "test-secret"}, stack
        )
        assert bundle.target.namespace == "abcdef"
        assert bundle.target.doh_url == "https://dns.nextdns.io/abcdef"
        assert bundle.capabilities.production_apply


@pytest.mark.parametrize("provider", ["adguardhome", "technitium"])
def test_selfhosted_binding_is_read_only_with_explicit_target(provider):
    from pathlib import Path

    from smartgate.config import load_config

    config = load_config(Path("config/dns/" + provider + ".example.toml"), {}).dns
    env = {
        "SMARTGATE_DNS_API_URL": "https://api.example",
        "SMARTGATE_DNS_NAMESPACE": "isolated.example",
        "SMARTGATE_DNS_DOH_URL": "https://resolver.example/dns-query",
        "SMARTGATE_DNS_API_KEY": "test-token",
        "SMARTGATE_DNS_USERNAME": "user",
        "SMARTGATE_DNS_PASSWORD": "pass",
    }
    with ExitStack() as stack:
        bundle = create_dns_bundle(config, env, stack)
        assert bundle.target.provider == provider
        assert not bundle.capabilities.production_apply
        from smartgate.errors import SafetyBlocked
        from smartgate.models import DesiredRewrite

        with pytest.raises(SafetyBlocked):
            bundle.provider.create(DesiredRewrite("probe.isolated.example", "8.8.8.8"))
