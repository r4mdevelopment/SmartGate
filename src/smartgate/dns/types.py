"""DNS target identity and adapter capability contracts."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from smartgate.dns.validation import validate_endpoint
from smartgate.errors import SafetyBlocked
from smartgate.models import DesiredRewrite, DnsCheck
from smartgate.ports import DnsProvider


@dataclass(frozen=True)
class DnsTarget:
    instance_id: str
    provider: str
    api_origin: str
    namespace: str
    doh_url: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.instance_id) is None:
            raise SafetyBlocked("Invalid DNS instance identifier")
        if not self.provider or not self.namespace or self.namespace != self.namespace.strip():
            raise SafetyBlocked("Invalid DNS target namespace")
        validate_endpoint(self.api_origin, origin_only=True)
        validate_endpoint(self.doh_url)


def target_fingerprint(target: DnsTarget) -> str:
    content = json.dumps(
        {
            "provider": target.provider,
            "api_origin": target.api_origin,
            "namespace": target.namespace,
            "doh_url": target.doh_url,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class DnsCapabilities:
    subtree: Literal["verified", "unverified", "unsupported"]
    identity: Literal["immutable", "marker", "value"]
    replace_mode: Literal["atomic", "delete_create", "unverified"]
    production_apply: bool


@dataclass(frozen=True)
class DnsBundle:
    provider: DnsProvider
    target: DnsTarget
    capabilities: DnsCapabilities
    verify: Callable[[tuple[DesiredRewrite, ...]], tuple[DnsCheck, ...]]
    legacy_state_key: str | None = None
