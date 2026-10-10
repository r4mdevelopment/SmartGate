"""Preconditions for isolated compatibility tests. Never configures the client DNS."""

import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from smartgate.catalog import normalize_domain
from smartgate.dns.credentials import resolve_nextdns_value
from smartgate.dns.types import DnsTarget, target_fingerprint
from smartgate.dns.validation import validate_endpoint
from smartgate.errors import ConfigError, SafetyBlocked


@dataclass(frozen=True)
class LiveSettings:
    test_profile_id: str = field(repr=False)
    active_profile_id: str = field(repr=False)
    api_key: str = field(repr=False)


def live_settings(env: Mapping[str, str]) -> LiveSettings:
    if env.get("SMARTGATE_LIVE") != "1" or env.get("NEXTDNS_TEST_ACK") != "isolated-profile-only":
        raise SafetyBlocked("Isolated live test requires explicit opt-in")
    test_id = env.get("NEXTDNS_TEST_PROFILE_ID", "").lower()
    active_id = env.get("NEXTDNS_ACTIVE_PROFILE_ID", "").lower()
    if not re.fullmatch(r"[0-9a-f]{6}", test_id):
        raise SafetyBlocked("A valid test profile ID must be explicitly provided")
    if active_id != "not-nextdns" and not re.fullmatch(r"[0-9a-f]{6}", active_id):
        raise SafetyBlocked("Provide the protected profile ID or explicitly attest external DNS")
    if test_id == active_id:
        raise SafetyBlocked("Live writes to the active profile are prohibited")
    try:
        key = resolve_nextdns_value("SMARTGATE_DNS_API_KEY", "NEXTDNS_API_KEY", env)
    except ConfigError:
        raise SafetyBlocked("API key is missing, invalid or conflicting") from None
    if not key or key != key.strip():
        raise SafetyBlocked("API key is missing or invalid")
    return LiveSettings(test_id, active_id, key)


def ensure_empty_profile(records: list[dict[str, object]]) -> None:
    if records:
        raise SafetyBlocked("Live compatibility requires an empty disposable profile")


def selfhosted_live_target(env: Mapping[str, str]) -> DnsTarget:
    if (
        env.get("SMARTGATE_LIVE") != "1"
        or env.get("SMARTGATE_DNS_TEST_ACK") != "isolated-target-only"
    ):
        raise SafetyBlocked("Self-hosted live test requires explicit isolated-target opt-in")
    protected = env.get("SMARTGATE_DNS_PROTECTED_FINGERPRINT", "")
    if re.fullmatch(r"[0-9a-f]{64}", protected) is None:
        raise SafetyBlocked("Protected DNS binding fingerprint must be explicitly supplied")
    provider = env.get("SMARTGATE_DNS_TEST_PROVIDER", "")
    if provider not in {"adguardhome", "technitium"}:
        raise SafetyBlocked("Self-hosted live test provider is unsupported")
    scope = env.get("SMARTGATE_DNS_TEST_NAMESPACE", "")
    try:
        if normalize_domain(scope) != scope:
            raise ConfigError("Invalid isolated scope")
    except ConfigError:
        raise SafetyBlocked("Live harness requires a canonical isolated test domain/zone") from None
    target = DnsTarget(
        "isolated-live",
        provider,
        env.get("SMARTGATE_DNS_TEST_API_URL", ""),
        scope,
        env.get("SMARTGATE_DNS_TEST_DOH_URL", ""),
    )
    protected_destination = mutation_destination_fingerprint(
        env.get("SMARTGATE_DNS_PROTECTED_PROVIDER", ""),
        env.get("SMARTGATE_DNS_PROTECTED_API_URL", ""),
        env.get("SMARTGATE_DNS_PROTECTED_NAMESPACE", ""),
    )
    test_destination = mutation_destination_fingerprint(provider, target.api_origin, scope)
    if test_destination == protected_destination or target_fingerprint(target) == protected:
        raise SafetyBlocked("Live writes to the protected DNS destination are prohibited")
    return target


def mutation_destination_fingerprint(provider: str, api_origin: str, namespace: str) -> str:
    if provider not in {"adguardhome", "technitium"}:
        raise SafetyBlocked("Protected mutation provider is unsupported")
    validate_endpoint(api_origin, origin_only=True)
    url = urlsplit(api_origin)
    host = (url.hostname or "").lower().rstrip(".")
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        scope = normalize_domain(namespace) if provider == "technitium" else ""
    except ConfigError:
        raise SafetyBlocked("Invalid protected mutation namespace") from None
    identity = [provider, host, url.port or 443, scope]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
