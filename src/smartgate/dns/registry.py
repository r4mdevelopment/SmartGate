"""Explicit registry of DNS adapter bindings."""

from collections.abc import Mapping
from contextlib import ExitStack

from smartgate.dns.adguardhome import AdGuardHomeProvider
from smartgate.dns.credentials import required_value, resolve_credentials, resolve_nextdns_value
from smartgate.dns.nextdns import NextDnsProvider
from smartgate.dns.technitium import TechnitiumProvider
from smartgate.dns.transport import ApiClient
from smartgate.dns.types import DnsBundle, DnsCapabilities, DnsTarget
from smartgate.dns.verify import DnsVerifier
from smartgate.errors import ConfigError
from smartgate.models import DnsConfig


def create_dns_bundle(config: DnsConfig, env: Mapping[str, str], stack: ExitStack) -> DnsBundle:
    if config.provider in {"adguardhome", "technitium"}:
        credentials = resolve_credentials(config.auth, env)
        target = DnsTarget(
            config.instance_id,
            config.provider,
            required_value(config.connection.api_url_env, env),
            required_value(config.connection.namespace_env, env),
            required_value(config.verify.doh_url_env, env),
        )
        client = ApiClient(
            target.api_origin,
            "basic" if config.provider == "adguardhome" else "bearer",
            credentials,
        )
        stack.callback(client.close)
        provider = (
            AdGuardHomeProvider(client)
            if config.provider == "adguardhome"
            else TechnitiumProvider(client, target.namespace)
        )
        verifier = DnsVerifier(target.doh_url)
        stack.callback(verifier.close)
        return DnsBundle(
            provider,
            target,
            DnsCapabilities("unverified", "value", "unverified", False),
            verifier.verify,
        )
    if config.provider != "nextdns":
        raise ConfigError("Unsupported DNS provider")

    profile_name = config.profile_id_env
    key_name = config.api_key_env
    if profile_name in {"NEXTDNS_PROFILE_ID", "SMARTGATE_DNS_PROFILE_ID"}:
        profile = resolve_nextdns_value("SMARTGATE_DNS_PROFILE_ID", "NEXTDNS_PROFILE_ID", env)
    else:
        profile = required_value(profile_name, env)
    if key_name in {"NEXTDNS_API_KEY", "SMARTGATE_DNS_API_KEY"}:
        key = resolve_nextdns_value("SMARTGATE_DNS_API_KEY", "NEXTDNS_API_KEY", env)
    else:
        key = required_value(key_name, env)
    nextdns_provider = NextDnsProvider(profile, key)
    stack.callback(nextdns_provider.close)
    target = DnsTarget(
        config.instance_id,
        "nextdns",
        "https://api.nextdns.io",
        profile,
        "https://dns.nextdns.io/" + profile,
    )
    verifier = DnsVerifier(target.doh_url)
    stack.callback(verifier.close)
    return DnsBundle(
        nextdns_provider,
        target,
        DnsCapabilities("verified", "immutable", "delete_create", True),
        verifier.verify,
        legacy_state_key=profile,
    )
