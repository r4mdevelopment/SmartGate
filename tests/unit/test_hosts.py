from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from smartgate.catalog import parse_catalog
from smartgate.errors import SourceError
from smartgate.models import ProviderConfig
from smartgate.providers.hosts import parse_hosts, validate_snapshot

NOW = datetime(2026, 10, 7, tzinfo=UTC)
CONFIG = ProviderConfig("geohide-ru", "geohide", region="RU")
CATALOG = parse_catalog(
    {
        "chatgpt": {
            "include_suffixes": ["chatgpt.com", "openai.com"],
            "required_domains": ["chatgpt.com"],
            "provider_order": ["geohide-ru"],
        }
    }
)


def pairs(snapshot):
    return [(c.domain, c.ip) for c in snapshot.candidates]


def test_hosts_preserve_distinct_pairs_and_normalize_aliases():
    text = (
        "\ufeff# header\r\n8.8.8.8 ChatGPT.COM. api.openai.com # aliases\r\n"
        "1.1.1.1 chatgpt.com\r\n8.8.8.8 chatgpt.com\r\n"
    )
    snapshot = parse_hosts(text, CONFIG, "fixture", NOW)
    assert pairs(snapshot) == [
        ("api.openai.com", "8.8.8.8"),
        ("chatgpt.com", "1.1.1.1"),
        ("chatgpt.com", "8.8.8.8"),
    ]
    reversed_text = "\n".join(reversed(text.lstrip("\ufeff").splitlines()))
    assert pairs(parse_hosts(reversed_text, CONFIG, "fixture", NOW)) == pairs(snapshot)
    assert all(c.port == 443 and c.transport == "tls_sni_tcp" for c in snapshot.candidates)
    assert not any(c.domain == "api.openai.com" and c.ip == "1.1.1.1" for c in snapshot.candidates)
    assert "source-provided endpoints; relay ownership and health unverified" in snapshot.warnings


@pytest.mark.parametrize(
    "ip",
    [
        "0.0.0.0",
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "192.0.2.1",
        "::1",
        "fc00::1",
        "ff02::1",
    ],
)
def test_nonpublic_and_blocking_hosts_entries_are_filtered(ip):
    snapshot = parse_hosts(f"{ip} chatgpt.com\n8.8.8.8 openai.com", CONFIG, "fixture", NOW)
    assert pairs(snapshot) == [("openai.com", "8.8.8.8")]


def test_idna_and_public_ipv6_are_preserved_as_candidates():
    snapshot = parse_hosts("2606:4700::1111 пример.рф\n8.8.8.8 chatgpt.com", CONFIG, "fixture", NOW)
    assert ("xn--e1afmkfd.xn--p1ai", "2606:4700::1111") in pairs(snapshot)


def test_invalid_line_ratio_has_an_exact_five_percent_boundary():
    good = ["8.8.8.8 chatgpt.com"] * 19
    assert parse_hosts("\n".join(good + ["bogus chatgpt.com"]), CONFIG, "fixture", NOW)
    with pytest.raises(SourceError):
        parse_hosts("\n".join(good[:18] + ["bogus chatgpt.com"]), CONFIG, "fixture", NOW)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# only comments",
        "<html>server error</html>",
        "8.8.8.8 https://chatgpt.com",
        "bogus chatgpt.com",
    ],
)
def test_empty_html_or_invalid_feed_is_blocked(text):
    with pytest.raises(SourceError):
        parse_hosts(text, CONFIG, "fixture", NOW)


def test_required_domain_and_selected_scope_are_enforced():
    snapshot = parse_hosts("8.8.8.8 chatgpt.com\n1.1.1.1 unrelated.com", CONFIG, "fixture", NOW)
    selected = validate_snapshot(snapshot, None, CATALOG, NOW)
    assert pairs(selected) == [("chatgpt.com", "8.8.8.8")]
    with pytest.raises(SourceError):
        validate_snapshot(
            parse_hosts("8.8.8.8 openai.com", CONFIG, "fixture", NOW), None, CATALOG, NOW
        )


def test_large_drop_is_blocked_relative_to_selected_pairs():
    old = parse_hosts(
        "8.8.8.8 chatgpt.com\n1.1.1.1 chatgpt.com\n9.9.9.9 chatgpt.com", CONFIG, "fixture", NOW
    )
    new = parse_hosts("8.8.8.8 chatgpt.com", CONFIG, "fixture", NOW)
    with pytest.raises(SourceError):
        validate_snapshot(new, old, CATALOG, NOW)


def test_stale_snapshot_is_retain_only_and_expires_after_a_day():
    snapshot = parse_hosts("8.8.8.8 chatgpt.com", CONFIG, "fixture", NOW)
    stale = validate_snapshot(snapshot, None, CATALOG, NOW + timedelta(hours=2))
    assert "stale: retain existing routes only" in stale.warnings
    assert stale.fetched_at == NOW
    with pytest.raises(SourceError):
        validate_snapshot(snapshot, None, CATALOG, NOW + timedelta(hours=25))
    with pytest.raises(SourceError):
        validate_snapshot(
            replace(snapshot, fetched_at=NOW + timedelta(hours=1)), None, CATALOG, NOW
        )


def test_source_drop_is_checked_per_service_not_only_globally():
    catalog = parse_catalog(
        {
            "chatgpt": {
                "include_suffixes": ["chatgpt.com"],
                "required_domains": ["chatgpt.com"],
                "provider_order": ["geohide-ru"],
            },
            "other": {
                "include_suffixes": ["openai.com"],
                "required_domains": ["openai.com"],
                "provider_order": ["geohide-ru"],
            },
        }
    )
    other = "\n".join(
        f"{ip} openai.com" for ip in ["8.8.8.8", "1.1.1.1", "9.9.9.9", "208.67.222.222"]
    )
    old = parse_hosts(
        other + "\n8.8.8.8 chatgpt.com\n1.1.1.1 chatgpt.com\n9.9.9.9 chatgpt.com",
        CONFIG,
        "fixture",
        NOW,
    )
    new = parse_hosts(other + "\n8.8.8.8 chatgpt.com", CONFIG, "fixture", NOW)
    with pytest.raises(SourceError, match="50 percent"):
        validate_snapshot(new, old, catalog, NOW)
