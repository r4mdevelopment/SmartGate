"""Explicit service scopes and DNS subtree publication rules."""

import ipaddress
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from smartgate.errors import ConfigError
from smartgate.models import ServicePolicy


def normalize_domain(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigError("Domain must be a hostname")
    raw = value[:-1] if value.endswith(".") else value
    try:
        domain = raw.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ConfigError("Invalid IDNA hostname") from None
    labels = domain.split(".")
    if (
        len(domain) > 253
        or len(labels) < 2
        or any(
            len(label) > 63 or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
            for label in labels
        )
    ):
        raise ConfigError("Invalid DNS hostname")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    raise ConfigError("IP addresses cannot be used as service domains")


def suffix_matches(domain: str, suffix: str) -> bool:
    return domain == suffix or domain.endswith("." + suffix)


def read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as file:
            return cast(dict[str, object], tomllib.load(file))
    except (OSError, tomllib.TOMLDecodeError):
        raise ConfigError("Configuration file cannot be read as TOML") from None


def table(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(f"{name} must be a TOML table")
    return cast(dict[str, object], value)


def only_keys(data: dict[str, object], allowed: set[str], name: str) -> None:
    if set(data) - allowed:
        raise ConfigError(f"{name} contains unsupported keys")


def boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be a boolean")
    return value


def strings(value: object, name: str, domains: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of strings")
    items = cast(list[str], value)
    normalized = tuple(normalize_domain(item) if domains else item for item in items)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise ConfigError(f"{name} must contain unique nonempty values")
    return normalized


@dataclass(frozen=True)
class ServiceCatalog:
    services: tuple[ServicePolicy, ...]

    def select(self, domain: str) -> ServicePolicy | None:
        domain = normalize_domain(domain)
        for service in self.services:
            if (
                service.enabled
                and any(suffix_matches(domain, suffix) for suffix in service.include_suffixes)
                and not any(suffix_matches(domain, suffix) for suffix in service.exclude_suffixes)
            ):
                return service
        return None

    def validate_publication(self, domain: str) -> None:
        domain = normalize_domain(domain)
        service = self.select(domain)
        if service is None:
            raise ConfigError("Domain is not selected by the service catalog")
        if not service.allow_subtree_rewrites:
            raise ConfigError("Subtree rewrites require explicit acceptance")
        if any(suffix_matches(exclusion, domain) for exclusion in service.exclude_suffixes):
            raise ConfigError("Parent rewrite would cover an excluded subtree")


def parse_catalog(data: dict[str, object]) -> ServiceCatalog:
    services: list[ServicePolicy] = []
    for name, raw in sorted(data.items()):
        entry = table(raw, "service")
        only_keys(
            entry,
            {
                "enabled",
                "include_suffixes",
                "required_domains",
                "exclude_suffixes",
                "provider_order",
                "allow_subtree_rewrites",
            },
            "service",
        )
        enabled = boolean(entry.get("enabled", True), "enabled")
        include = strings(entry.get("include_suffixes", []), "include_suffixes", domains=True)
        required = strings(entry.get("required_domains", []), "required_domains", domains=True)
        excluded = strings(entry.get("exclude_suffixes", []), "exclude_suffixes", domains=True)
        order = strings(entry.get("provider_order", []), "provider_order")
        subtree = boolean(entry.get("allow_subtree_rewrites", False), "allow_subtree_rewrites")
        if enabled and not include:
            raise ConfigError("Enabled service must select domains")
        for domain in required:
            if not any(suffix_matches(domain, suffix) for suffix in include) or any(
                suffix_matches(domain, suffix) for suffix in excluded
            ):
                raise ConfigError("Required domain must be selected and cannot be excluded")
        services.append(ServicePolicy(name, enabled, include, required, excluded, order, subtree))
    if not services:
        raise ConfigError("Service catalog cannot be empty")
    for index, left in enumerate(services):
        for right in services[index + 1 :]:
            if not left.enabled or not right.enabled:
                continue
            overlaps = any(
                suffix_matches(a, b) or suffix_matches(b, a)
                for a in left.include_suffixes
                for b in right.include_suffixes
            )
            if overlaps and (
                left.provider_order != right.provider_order
                or left.allow_subtree_rewrites != right.allow_subtree_rewrites
                or left.exclude_suffixes != right.exclude_suffixes
            ):
                raise ConfigError("Overlapping services have conflicting routing policies")
    return ServiceCatalog(tuple(services))


def load_catalog(path: Path) -> ServiceCatalog:
    data = read_toml(path)
    only_keys(data, {"services"}, "catalog")
    return parse_catalog(table(data.get("services", {}), "services"))
