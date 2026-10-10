"""Explicit local manifest for already deployed TLS/SNI relays."""

import hashlib
import json
from datetime import datetime
from typing import cast

from smartgate.catalog import normalize_domain
from smartgate.errors import ConfigError, SourceError
from smartgate.models import ProviderConfig, RelayCandidate, RelaySnapshot
from smartgate.providers.hosts import MAX_SOURCE_BYTES, SOURCE_WARNING, public_address


def object_table(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise SourceError("Manifest contains missing or unsupported fields")
    return cast(dict[str, object], value)


class ManifestRelayProvider:
    def fetch(self, config: ProviderConfig, now: datetime) -> RelaySnapshot:
        if config.type != "selfhosted" or config.manifest_path is None or now.tzinfo is None:
            raise SourceError("Selfhosted provider needs a manifest and aware clock")
        try:
            with config.manifest_path.open("rb") as file:
                content = file.read(MAX_SOURCE_BYTES + 1)
            if len(content) > MAX_SOURCE_BYTES:
                raise SourceError("Relay manifest exceeds 5 MiB")
            data = object_table(
                json.loads(content.decode("utf-8-sig")),
                {"schema_version", "provider_id", "endpoints"},
            )
        except (OSError, UnicodeError, ValueError, RecursionError):
            raise SourceError("Relay manifest cannot be read as JSON") from None
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise SourceError("Unsupported manifest schema")
        if data["provider_id"] != config.id:
            raise SourceError("Manifest provider ID does not match configuration")
        endpoints = data["endpoints"]
        if not isinstance(endpoints, list) or not endpoints:
            raise SourceError("Manifest must contain endpoints")
        pairs: set[tuple[str, str]] = set()
        for raw in endpoints:
            item = object_table(raw, {"ip", "port", "transport", "domains"})
            if type(item["port"]) is not int or item["port"] != 443:
                raise SourceError("Relay must use TCP port 443")
            if item["transport"] != "tls_sni_tcp" or not isinstance(item["ip"], str):
                raise SourceError("Unsupported relay transport or IP")
            ip = public_address(item["ip"])
            if ip is None:
                raise SourceError("Manifest endpoints must use public IPs")
            domains = item["domains"]
            if not isinstance(domains, list) or not domains:
                raise SourceError("Manifest endpoints must explicitly list domains")
            for value in domains:
                if not isinstance(value, str):
                    raise SourceError("Manifest domain must be a hostname")
                try:
                    domain = normalize_domain(value)
                except ConfigError:
                    raise SourceError("Invalid manifest domain") from None
                pairs.add((domain, ip))
        source_id = "manifest:" + config.manifest_path.name
        candidates = tuple(
            RelayCandidate(
                config.id, domain, ip, 443, "tls_sni_tcp", config.priority, source_id, None
            )
            for domain, ip in sorted(pairs)
        )
        return RelaySnapshot(
            config.id, now, hashlib.sha256(content).hexdigest(), candidates, (SOURCE_WARNING,)
        )
