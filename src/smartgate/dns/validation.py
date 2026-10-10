"""Provider-independent rewrite and HTTPS endpoint validation."""

import ipaddress
from urllib.parse import urlsplit

from smartgate.catalog import normalize_domain
from smartgate.errors import ConfigError, SafetyBlocked
from smartgate.models import DesiredRewrite


def validate_desired(record: DesiredRewrite) -> None:
    try:
        address = ipaddress.ip_address(record.ipv4)
        valid = (
            normalize_domain(record.domain) == record.domain
            and address.version == 4
            and address.is_global
            and not address.is_multicast
            and not address.is_reserved
        )
    except (ValueError, ConfigError):
        valid = False
    if not valid:
        raise SafetyBlocked("Rewrite requires a canonical domain and public IPv4")


def validate_endpoint(value: str, *, origin_only: bool = False) -> str:
    try:
        url = urlsplit(value)
        port = url.port
        valid = (
            value == value.strip()
            and not any(ord(c) < 33 or ord(c) == 127 for c in value)
            and "\\" not in value
            and url.scheme == "https"
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and "?" not in value
            and "#" not in value
            and (port is None or 1 <= port <= 65535)
            and (not origin_only or url.path in {"", "/"})
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise SafetyBlocked("Invalid HTTPS endpoint")
    return value
