import json
from pathlib import Path

import httpx
import pytest

from smartgate.dns.nextdns import NextDnsProvider
from smartgate.errors import DnsApiError, RecoveryRequired, SafetyBlocked
from smartgate.http import HttpClient
from smartgate.models import DesiredRewrite, DnsRecord


def provider(handler):
    elapsed = [0.0]
    http = HttpClient(
        "unit-test-key",
        transport=httpx.MockTransport(handler),
        clock=lambda: elapsed[0],
        sleep=lambda s: elapsed.__setitem__(0, elapsed[0] + s),
        jitter=lambda: 0,
    )
    return NextDnsProvider("abcdef", "unit-test-key", http)


def test_live_fixture_create_and_delete_readback():
    fixture = json.loads(Path("tests/fixtures/nextdns/compatibility.json").read_text())
    created = fixture["observations"][0]["result"]
    actual = []
    seen = []

    def handler(request):
        assert request.url.host == "api.nextdns.io"
        seen.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"data": actual})
        if request.method == "POST":
            assert json.loads(request.content) == {
                "name": "relay-test.example.com",
                "content": "8.8.8.8",
            }
            actual.append(created["data"])
            return httpx.Response(200, json=created)
        assert request.method == "DELETE"
        assert request.url.path.endswith("/record-1")
        actual.clear()
        return httpx.Response(204)

    dns = provider(handler)
    result = dns.create(DesiredRewrite("relay-test.example.com", "8.8.8.8"))
    assert result == DnsRecord("record-1", "relay-test.example.com", "8.8.8.8")
    dns.delete(result)
    assert actual == [] and seen == ["POST", "GET", "GET", "DELETE", "GET"]


def test_paginated_records_include_foreign_ipv6_content():
    pages = iter(
        [
            {
                "data": [{"id": "a", "name": "example.com", "content": "8.8.8.8", "type": "A"}],
                "meta": {"pagination": {"cursor": "next"}},
            },
            {
                "data": [
                    {
                        "id": "b",
                        "name": "other.example",
                        "content": "2606:4700::1111",
                        "type": "AAAA",
                    }
                ]
            },
        ]
    )
    dns = provider(lambda r: httpx.Response(200, json=next(pages)))
    assert len(dns.list_records()) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"data": [{"id": "a", "name": "example.com"}]},
        {"data": [{"id": "", "name": "example.com", "content": "8.8.8.8"}]},
        {"data": [{"id": "a", "name": "https://example.com", "content": "8.8.8.8"}]},
        {"data": [{"id": "a", "name": "example.com", "content": "8.8.8.8"}] * 2},
        {"data": {}},
    ],
)
def test_invalid_list_payload_blocks(payload):
    with pytest.raises(DnsApiError):
        provider(lambda r: httpx.Response(200, json=payload)).list_records()


def test_create_missing_or_mismatched_id_requires_recovery_without_retry():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"data": {"name": "example.com", "content": "8.8.8.8"}})

    with pytest.raises(RecoveryRequired) as error:
        provider(handler).create(DesiredRewrite("example.com", "8.8.8.8"))
    assert calls == ["POST"]
    assert error.value.operation == "create"
    assert error.value.after == DesiredRewrite("example.com", "8.8.8.8")


def test_replace_failure_after_delete_has_before_after_context():
    before = DnsRecord("old", "example.com", "8.8.8.8")
    after = DesiredRewrite("example.com", "1.1.1.1")
    actual = [{"id": before.record_id, "name": before.domain, "content": before.content}]
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"data": actual})
        if request.method == "DELETE":
            actual.clear()
            return httpx.Response(204)
        raise httpx.ReadTimeout("private response")

    with pytest.raises(RecoveryRequired) as error:
        provider(handler).replace(before, after)
    assert calls.count("POST") == 1
    assert (
        error.value.operation == "replace"
        and error.value.before == before
        and error.value.after == after
    )
    assert "private response" not in str(error.value)


def test_drift_blocks_delete_before_mutation():
    before = DnsRecord("old", "example.com", "8.8.8.8")
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(
            200, json={"data": [{"id": "old", "name": "example.com", "content": "1.1.1.1"}]}
        )

    with pytest.raises(SafetyBlocked):
        provider(handler).delete(before)
    assert methods == ["GET"]


@pytest.mark.parametrize("ipv4", ["127.0.0.1", "203.0.113.1", "2606:4700::1111", "invalid"])
def test_bad_desired_is_rejected_before_network(ipv4):
    with pytest.raises(SafetyBlocked):
        provider(lambda r: pytest.fail("must not send")).create(DesiredRewrite("example.com", ipv4))


def test_successful_post_missing_readback_is_not_success():
    calls = []

    def handler(request):
        calls.append(request.method)
        return (
            httpx.Response(
                200,
                json={
                    "data": {"id": "new", "name": "example.com", "content": "8.8.8.8", "type": "A"}
                },
            )
            if request.method == "POST"
            else httpx.Response(200, json={"data": []})
        )

    with pytest.raises(RecoveryRequired):
        provider(handler).create(DesiredRewrite("example.com", "8.8.8.8"))
    assert calls == ["POST", "GET"]


def test_full_400_call_transport_budget_obeys_30_per_minute():
    elapsed = [0.0]
    timestamps = []

    def handler(request):
        timestamps.append(elapsed[0])
        return httpx.Response(200, json={"data": []})

    http = HttpClient(
        "unit-test-key",
        transport=httpx.MockTransport(handler),
        clock=lambda: elapsed[0],
        sleep=lambda s: elapsed.__setitem__(0, elapsed[0] + s),
    )
    dns = NextDnsProvider("abcdef", "unit-test-key", http)
    for _ in range(400):
        assert dns.list_records() == ()
    with pytest.raises(SafetyBlocked):
        dns.list_records()
    assert len(timestamps) == 400 and timestamps[-1] == 798
    assert max(sum(start <= t < start + 60 for t in timestamps) for start in timestamps) == 30


def test_rate_limit_retry_after_obeys_delay_and_returns_records():
    elapsed = [0.0]
    times = []

    def handler(request):
        times.append(elapsed[0])
        return (
            httpx.Response(429, headers={"Retry-After": "7"})
            if len(times) == 1
            else httpx.Response(200, json={"data": []})
        )

    http = HttpClient(
        "unit-test-key",
        transport=httpx.MockTransport(handler),
        clock=lambda: elapsed[0],
        sleep=lambda s: elapsed.__setitem__(0, elapsed[0] + s),
    )
    assert NextDnsProvider("abcdef", "unit-test-key", http).list_records() == ()
    assert times == [0, 7]


def test_replace_confirms_new_id_after_delete_create():
    actual = [{"id": "old", "name": "example.com", "content": "8.8.8.8", "type": "A"}]
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"data": actual})
        if request.method == "DELETE":
            actual.clear()
            return httpx.Response(204)
        actual.append({"id": "new", "name": "example.com", "content": "1.1.1.1", "type": "A"})
        return httpx.Response(200, json={"data": actual[0]})

    result = provider(handler).replace(
        DnsRecord("old", "example.com", "8.8.8.8"), DesiredRewrite("example.com", "1.1.1.1")
    )
    assert result == DnsRecord("new", "example.com", "1.1.1.1")
    assert calls == ["GET", "DELETE", "GET", "POST", "GET"]


def test_post_errors_envelope_and_unsupported_type_are_safe():
    with pytest.raises(DnsApiError):
        provider(
            lambda r: httpx.Response(200, json={"errors": [{"detail": "unit-secret"}]})
        ).create(DesiredRewrite("example.com", "8.8.8.8"))
    with pytest.raises(RecoveryRequired):
        provider(
            lambda r: httpx.Response(
                200,
                json={
                    "data": {
                        "id": "new",
                        "name": "example.com",
                        "content": "8.8.8.8",
                        "type": "AAAA",
                    }
                },
            )
        ).create(DesiredRewrite("example.com", "8.8.8.8"))


def test_unknown_delete_in_replace_keeps_complete_recovery_context():
    before = DnsRecord("old", "example.com", "8.8.8.8")
    after = DesiredRewrite("example.com", "1.1.1.1")
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200, json={"data": [{"id": "old", "name": "example.com", "content": "8.8.8.8"}]}
            )
        raise httpx.ReadTimeout("unit-secret")

    with pytest.raises(RecoveryRequired) as error:
        provider(handler).replace(before, after)
    assert methods == ["GET", "DELETE"]
    assert (
        error.value.operation == "replace"
        and error.value.before == before
        and error.value.after == after
    )
