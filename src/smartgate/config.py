"""Strict typed TOML validation. Loading never performs network or state writes."""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from smartgate.catalog import (
    ServiceCatalog,
    boolean,
    load_catalog,
    only_keys,
    parse_catalog,
    read_toml,
    table,
)
from smartgate.errors import ConfigError
from smartgate.models import (
    DnsAuthConfig,
    DnsConfig,
    DnsConnectionConfig,
    DnsVerifyConfig,
    ProbePolicy,
    ProviderConfig,
    StateConfig,
    SyncPolicy,
)


@dataclass(frozen=True)
class AppConfig:
    schema_version: int
    dns: DnsConfig
    state: StateConfig
    providers: tuple[ProviderConfig, ...]
    catalog_path: Path | None
    probe: ProbePolicy
    sync: SyncPolicy
    catalog: ServiceCatalog


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigError(f"{name} must be a nonempty string")
    return value


def integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigError(f"{name} is outside its integer range")
    return value


def number(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a finite number")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} is outside its numeric range")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ConfigError(f"{name} is outside its numeric range")
    return result


def env_name(value: object, name: str) -> str:
    result = text(value, name)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", result) is None:
        raise ConfigError(f"{name} must name an environment variable")
    return result


def provider_config(raw: object, base: Path) -> ProviderConfig:
    data = table(raw, "relay provider")
    only_keys(data, {"id", "type", "region", "manifest_path", "priority"}, "relay provider")
    identifier = text(data.get("id"), "provider id")
    if re.fullmatch(r"[a-z][a-z0-9-]*", identifier) is None:
        raise ConfigError("Provider ID must be a short lowercase name")
    kind = text(data.get("type"), "provider type")
    priority = integer(data.get("priority", 0), "priority", 0, 10000)
    if kind == "geohide":
        region = text(data.get("region", "RU"), "region")
        if region not in {"RU", "EU", "US"} or "manifest_path" in data:
            raise ConfigError("GeoHide needs a supported region and no manifest")
        return ProviderConfig(identifier, "geohide", region=region, priority=priority)
    if kind == "selfhosted":
        if "region" in data:
            raise ConfigError("Selfhosted region is described by its manifest")
        path = (base / text(data.get("manifest_path"), "manifest_path")).resolve()
        return ProviderConfig(identifier, "selfhosted", manifest_path=path, priority=priority)
    raise ConfigError("Unsupported relay provider type")


def load_config(path: Path, env: Mapping[str, str]) -> AppConfig:
    # Mode-specific adapters resolve credentials later. Never retain the supplied env.
    del env
    path = path.resolve()
    data = read_toml(path)
    only_keys(
        data,
        {
            "schema_version",
            "dns",
            "state",
            "relay",
            "services",
            "catalog_path",
            "probe",
            "sync",
        },
        "configuration",
    )
    version = integer(data.get("schema_version"), "schema_version", 1, 2)
    dns_config = parse_dns_config(table(data.get("dns", {}), "dns"), version)
    state = table(data.get("state", {}), "state")
    only_keys(state, {"provider", "branch", "path"}, "state")
    state_kind = text(state.get("provider", "local"), "state provider")
    if state_kind not in {"local", "git_branch"}:
        raise ConfigError("Unsupported state provider")
    branch = text(state.get("branch", "smartgate-state"), "state branch")
    if re.fullmatch(r"[a-z][a-z0-9-]*", branch) is None:
        raise ConfigError("State branch must be a short lowercase name")
    state_config = StateConfig(
        "local" if state_kind == "local" else "git_branch",
        branch,
        (path.parent / text(state.get("path", ".smartgate/state.json"), "state path")).resolve(),
    )
    relay = table(data.get("relay", {}), "relay")
    only_keys(relay, {"providers"}, "relay")
    raw_providers = relay.get("providers", [])
    if not isinstance(raw_providers, list) or not raw_providers:
        raise ConfigError("At least one relay provider is required")
    providers = tuple(provider_config(item, path.parent) for item in raw_providers)
    identifiers = {provider.id for provider in providers}
    if len(identifiers) != len(providers):
        raise ConfigError("Provider IDs must be unique")
    catalog_path = None
    if "catalog_path" in data:
        if "services" in data:
            raise ConfigError("Use an external catalog or inline services, not both")
        catalog_path = (path.parent / text(data["catalog_path"], "catalog_path")).resolve()
        catalog = load_catalog(catalog_path)
    else:
        catalog = parse_catalog(table(data.get("services", {}), "services"))
    for service in catalog.services:
        if service.enabled and not service.provider_order:
            raise ConfigError("Enabled service requires an explicit provider order")
        if any(identifier not in identifiers for identifier in service.provider_order):
            raise ConfigError("Service references an unknown relay provider")
    probe = table(data.get("probe", {}), "probe")
    only_keys(
        probe,
        {
            "attempts",
            "timeout_s",
            "concurrency",
            "max_candidates_per_domain",
            "max_pairs",
        },
        "probe",
    )
    probe_policy = ProbePolicy(
        integer(probe.get("attempts", 2), "attempts", 1, 2),
        number(probe.get("timeout_s", 4.0), "timeout_s", 0.1, 4.0),
        integer(probe.get("concurrency", 8), "concurrency", 1, 8),
        integer(probe.get("max_candidates_per_domain", 2), "max_candidates_per_domain", 1, 2),
        integer(probe.get("max_pairs", 200), "max_pairs", 1, 200),
    )
    sync = table(data.get("sync", {}), "sync")
    only_keys(
        sync,
        {
            "enabled",
            "allow_prune",
            "max_records",
            "max_mutations",
            "max_change_ratio",
        },
        "sync",
    )
    sync_policy = SyncPolicy(
        boolean(sync.get("enabled", False), "sync enabled"),
        boolean(sync.get("allow_prune", False), "allow_prune"),
        integer(sync.get("max_records", 100), "max_records", 1, 100),
        integer(sync.get("max_mutations", 20), "max_mutations", 1, 200),
        number(sync.get("max_change_ratio", 0.25), "max_change_ratio", 0.0, 1.0),
    )
    return AppConfig(
        version,
        dns_config,
        state_config,
        providers,
        catalog_path,
        probe_policy,
        sync_policy,
        catalog,
    )


def parse_dns_config(data: dict[str, object], version: int) -> DnsConfig:
    if version == 1:
        only_keys(data, {"provider", "profile_id_env", "api_key_env"}, "dns")
        if data.get("provider", "nextdns") != "nextdns":
            raise ConfigError("Legacy DNS configuration supports only NextDNS")
        profile = env_name(data.get("profile_id_env", "NEXTDNS_PROFILE_ID"), "profile_id_env")
        key = env_name(data.get("api_key_env", "NEXTDNS_API_KEY"), "api_key_env")
        return DnsConfig("nextdns", profile, key, auth=DnsAuthConfig("api_key", api_key_env=key))
    only_keys(data, {"provider", "instance_id", "connection", "auth", "verify"}, "dns")
    provider = text(data.get("provider"), "DNS provider")
    if provider not in {"nextdns", "adguardhome", "technitium"}:
        raise ConfigError("Unsupported DNS provider")
    instance = text(data.get("instance_id"), "DNS instance_id")
    if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", instance) is None:
        raise ConfigError("Invalid DNS instance_id")
    connection = table(data.get("connection", {}), "dns.connection")
    only_keys(connection, {"api_url_env", "namespace_env"}, "dns.connection")
    verify = table(data.get("verify", {}), "dns.verify")
    only_keys(verify, {"doh_url_env"}, "dns.verify")
    auth = table(data.get("auth", {}), "dns.auth")
    only_keys(auth, {"kind", "api_key_env", "username_env", "password_env"}, "dns.auth")
    expected = {"nextdns": "api_key", "adguardhome": "basic", "technitium": "bearer"}[provider]
    if auth.get("kind") != expected:
        raise ConfigError("DNS authentication does not match the provider")
    if expected == "basic":
        only_keys(auth, {"kind", "username_env", "password_env"}, "dns.auth")
        auth_config = DnsAuthConfig(
            "basic",
            username_env=env_name(auth.get("username_env"), "username_env"),
            password_env=env_name(auth.get("password_env"), "password_env"),
        )
    else:
        only_keys(auth, {"kind", "api_key_env"}, "dns.auth")
        auth_config = DnsAuthConfig(
            "api_key" if provider == "nextdns" else "bearer",
            api_key_env=env_name(auth.get("api_key_env", "SMARTGATE_DNS_API_KEY"), "api_key_env"),
        )
    if provider == "nextdns":
        only_keys(connection, {"namespace_env"}, "dns.connection")
        only_keys(verify, set(), "dns.verify")
        connection_config = DnsConnectionConfig(
            namespace_env=env_name(
                connection.get("namespace_env", "SMARTGATE_DNS_PROFILE_ID"), "namespace_env"
            )
        )
        verify_config = DnsVerifyConfig()
    else:
        connection_config = DnsConnectionConfig(
            env_name(connection.get("api_url_env"), "api_url_env"),
            env_name(connection.get("namespace_env"), "namespace_env"),
        )
        verify_config = DnsVerifyConfig(env_name(verify.get("doh_url_env"), "doh_url_env"))
    return DnsConfig(
        provider,
        connection_config.namespace_env or "",
        auth_config.api_key_env or "",
        instance,
        connection_config,
        auth_config,
        verify_config,
        False,
    )
