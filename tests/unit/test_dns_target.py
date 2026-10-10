from dataclasses import replace

import pytest

from smartgate.dns.types import DnsTarget, target_fingerprint
from smartgate.errors import SafetyBlocked


def target():
    return DnsTarget(
        "primary", "nextdns", "https://api.nextdns.io", "abcdef", "https://dns.example/dns-query"
    )


def test_binding_changes_when_destination_changes_but_not_instance_path():
    original = target()
    assert target_fingerprint(original) == target_fingerprint(
        replace(original, instance_id="other")
    )
    for field, value in (
        ("provider", "adguardhome"),
        ("api_origin", "https://other.example"),
        ("namespace", "other"),
        ("doh_url", "https://other.example/dns-query"),
    ):
        assert target_fingerprint(original) != target_fingerprint(
            replace(original, **{field: value})
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://dns.example",
        "https://user:secret@dns.example/query",
        "https://dns.example/query?token=secret",
        "https://dns.example/#secret",
        "https://dns.example:bad/query",
        "https://dns.example/\\evil",
        " https://dns.example",
    ],
)
def test_unsafe_doh_destination_is_rejected(url):
    with pytest.raises(SafetyBlocked):
        replace(target(), doh_url=url)


@pytest.mark.parametrize("identifier", ["../other", "Primary", "a" * 65, "", "1primary"])
def test_state_path_cannot_escape_instance_namespace(identifier):
    with pytest.raises(SafetyBlocked):
        replace(target(), instance_id=identifier)
