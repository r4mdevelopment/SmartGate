import json
from datetime import UTC, datetime

import httpx
import pytest

from smartgate.errors import SourceError
from smartgate.models import ProviderConfig
from smartgate.providers.geohide import GeoHideProvider, SourceClient
from smartgate.providers.hosts import MAX_SOURCE_BYTES, parse_hosts
from smartgate.providers.manifest import ManifestRelayProvider
from smartgate.providers.registry import create_relay_provider

NOW = datetime(2026, 10, 7, tzinfo=UTC)


def manifest(ip="8.8.8.8", **endpoint):
    return {
        "schema_version": 1,
        "provider_id": "own",
        "endpoints": [
            dict(
                {"ip": ip, "port": 443, "transport": "tls_sni_tcp", "domains": ["chatgpt.com"]},
                **endpoint,
            ),
        ],
    }


def test_manifest_and_hosts_have_the_same_relay_contract(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8")
    config = ProviderConfig("own", "selfhosted", manifest_path=path)
    own = ManifestRelayProvider().fetch(config, NOW)
    hosts = parse_hosts(
        "8.8.8.8 chatgpt.com", ProviderConfig("gh", "geohide", region="RU"), "fixture", NOW
    )

    def common(snapshot):
        return [(c.domain, c.ip, c.port, c.transport) for c in snapshot.candidates]

    assert common(own) == common(hosts)
    assert own.provider_id == "own" and hosts.provider_id == "gh"


@pytest.mark.parametrize(
    "changes",
    [
        {"port": 8443},
        {"port": True},
        {"transport": "socks"},
        {"domains": ["not a domain"]},
        {"domains": []},
        {"extra": "x"},
    ],
)
def test_manifest_rejects_unsupported_endpoints(tmp_path, changes):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(manifest(**changes)), encoding="utf-8")
    with pytest.raises(SourceError):
        ManifestRelayProvider().fetch(ProviderConfig("own", "selfhosted", manifest_path=path), NOW)


@pytest.mark.parametrize("ip", ["10.0.0.1", "127.0.0.1", "bogus", "192.0.2.1"])
def test_manifest_rejects_nonpublic_addresses(tmp_path, ip):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(manifest(ip)), encoding="utf-8")
    with pytest.raises(SourceError):
        ManifestRelayProvider().fetch(ProviderConfig("own", "selfhosted", manifest_path=path), NOW)


def test_manifest_provider_id_and_schema_are_checked(tmp_path):
    path = tmp_path / "bad.json"
    for changes in [
        {"provider_id": "other"},
        {"schema_version": 2},
        {"schema_version": True},
        {"endpoints": []},
    ]:
        path.write_text(json.dumps(dict(manifest(), **changes)), encoding="utf-8")
        with pytest.raises(SourceError):
            ManifestRelayProvider().fetch(
                ProviderConfig("own", "selfhosted", manifest_path=path), NOW
            )


def test_registry_selects_adapters_without_fetching():
    assert isinstance(
        create_relay_provider(ProviderConfig("own", "selfhosted")), ManifestRelayProvider
    )
    source = SourceClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    assert isinstance(
        create_relay_provider(ProviderConfig("gh", "geohide", region="EU"), source), GeoHideProvider
    )
    source.close()


@pytest.mark.parametrize(
    "region,path",
    [(region, f"/static/formats/{region.lower()}/hosts.txt") for region in ("RU", "EU", "US")],
)
def test_geohide_fixed_region_source_and_no_credential_header(region, path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="8.8.8.8 chatgpt.com")

    source = SourceClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)
    config = ProviderConfig("gh", "geohide", region=region)
    snapshot = GeoHideProvider(source).fetch(config, NOW)
    assert len(snapshot.candidates) == 1
    assert seen[0].url.scheme == "https"
    assert seen[0].url.host == "dns.geohide.ru"
    assert seen[0].url.port == 8443
    assert seen[0].url.path == path
    assert not seen[0].url.query
    assert "Authorization" not in seen[0].headers
    assert "X-Api-Key" not in seen[0].headers
    source.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://other.example/feed"}),
        httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html>Error</html>"),
        httpx.Response(200, content=b"x" * (MAX_SOURCE_BYTES + 1)),
        httpx.Response(
            200, headers={"Content-Length": str(MAX_SOURCE_BYTES + 1)}, content=b"small"
        ),
        httpx.Response(200, content=b"\xff"),
    ],
)
def test_invalid_download_is_blocked(response):
    source = SourceClient(
        transport=httpx.MockTransport(lambda request: response), sleep=lambda _: None
    )
    with pytest.raises(SourceError):
        GeoHideProvider(source).fetch(ProviderConfig("gh", "geohide", region="RU"), NOW)
    source.close()


def test_get_retries_are_bounded_and_redirect_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    source = SourceClient(transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(SourceError):
        GeoHideProvider(source).fetch(ProviderConfig("gh", "geohide", region="RU"), NOW)
    assert len(calls) == 3
    source.close()


def test_source_client_rejects_unregistered_urls_before_network():
    source = SourceClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("must not connect"))
    )
    with pytest.raises(SourceError):
        source.get_text("https://other.example/feed")
    source.close()


def test_curated_geohide_fixture_matches_equivalent_manifest(tmp_path):
    from pathlib import Path

    path = Path(__file__).parents[1] / "fixtures" / "hosts" / "geohide-chatgpt.hosts"
    hosts = parse_hosts(
        path.read_text(encoding="utf-8"),
        ProviderConfig("gh", "geohide", region="RU"),
        "fixture",
        NOW,
    )
    manifest_path = tmp_path / "own.json"
    data = {
        "schema_version": 1,
        "provider_id": "own",
        "endpoints": [
            {
                "ip": candidate.ip,
                "port": candidate.port,
                "transport": candidate.transport,
                "domains": [candidate.domain],
            }
            for candidate in hosts.candidates
        ],
    }
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    own = ManifestRelayProvider().fetch(
        ProviderConfig("own", "selfhosted", manifest_path=manifest_path), NOW
    )
    assert [(c.domain, c.ip, c.port, c.transport) for c in own.candidates] == [
        (c.domain, c.ip, c.port, c.transport) for c in hosts.candidates
    ]


def test_streaming_size_limit_applies_without_content_length():
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(81):
                yield b"x" * 65536

    source = SourceClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))
    )
    with pytest.raises(SourceError, match="5 MiB"):
        source.get_text("https://dns.geohide.ru:8443/static/formats/ru/hosts.txt")
    source.close()


def test_manifest_read_has_a_size_limit(tmp_path):
    path = tmp_path / "huge.json"
    path.write_bytes(b" " * (MAX_SOURCE_BYTES + 1))
    with pytest.raises(SourceError, match="5 MiB"):
        ManifestRelayProvider().fetch(ProviderConfig("own", "selfhosted", manifest_path=path), NOW)
