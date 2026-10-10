import json

import httpx
import pytest

from smartgate.dns.adguardhome import AdGuardHomeProvider
from smartgate.dns.transport import ApiClient
from smartgate.errors import DnsApiError, RecoveryRequired, SafetyBlocked
from smartgate.models import DesiredRewrite, DnsCredentials


def backend(records=None, case=None, allow=True):
    entries = list(records or [])
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "api.example"
        assert request.headers["authorization"] == "Basic dXNlcjpwYXNz"
        if case == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.example"})
        if case == "timeout":
            raise httpx.ReadTimeout("secret-transport-text")
        if request.method == "GET":
            return httpx.Response(200, json=entries)
        data = json.loads(request.content)
        if request.url.path.endswith("/add"):
            entries.append(data)
        elif request.url.path.endswith("/delete"):
            entries.remove(data)
        elif request.url.path.endswith("/update"):
            index = entries.index(data["target"])
            entries[index] = data["update"]
        if case == "lost_mutation":
            raise httpx.ReadError("secret-transport-text")
        return httpx.Response(200)

    client = ApiClient(
        "https://api.example",
        "basic",
        DnsCredentials(username="user", password="pass"),
        transport=httpx.MockTransport(handler),
    )
    return AdGuardHomeProvider(client, allow_reference_writes=allow), entries, seen


def test_reference_crud_uses_official_payloads_and_readback():
    api, entries, seen = backend()
    record = api.create(DesiredRewrite("example.com", "8.8.8.8"))
    assert entries == [{"domain": "example.com", "answer": "8.8.8.8", "enabled": True}]
    new = api.replace(record, DesiredRewrite("example.com", "1.1.1.1"))
    assert new.content == "1.1.1.1" and new != record
    assert entries == [{"domain": "example.com", "answer": "1.1.1.1", "enabled": True}]
    api.delete(new)
    assert entries == []
    assert [r.url.path for r in seen if r.method != "GET"] == [
        "/control/rewrite/add",
        "/control/rewrite/update",
        "/control/rewrite/delete",
    ]


@pytest.mark.parametrize(
    "entries",
    [
        [{"domain": "example.com", "answer": "8.8.8.8", "enabled": False}],
        [{"domain": "example.com", "answer": "8.8.8.8"}] * 2,
    ],
)
def test_disabled_or_ambiguous_rules_block_instead_of_disappearing(entries):
    api, _, _ = backend(entries)
    with pytest.raises(DnsApiError):
        api.list_records()


def test_foreign_ipv6_and_cname_remain_visible():
    api, _, _ = backend(
        [
            {"domain": "ipv6.example", "answer": "2606:4700::1111"},
            {"domain": "alias.example", "answer": "foreign.example"},
        ]
    )
    assert {r.content for r in api.list_records()} == {"2606:4700::1111", "foreign.example"}


def test_value_identity_never_authorizes_runtime_writes():
    api, _, seen = backend(allow=False)
    with pytest.raises(SafetyBlocked):
        api.create(DesiredRewrite("example.com", "8.8.8.8"))
    assert seen == []


def test_lost_mutation_response_requires_recovery_and_is_not_retried():
    api, _, seen = backend(case="lost_mutation")
    with pytest.raises(RecoveryRequired):
        api.create(DesiredRewrite("example.com", "8.8.8.8"))
    assert sum(r.method == "POST" for r in seen) == 1


@pytest.mark.parametrize("case", ["redirect", "timeout"])
def test_transport_failure_does_not_follow_redirect_or_leak_raw_text(case):
    api, _, seen = backend(case=case)
    with pytest.raises(DnsApiError) as error:
        api.list_records()
    assert "secret-transport-text" not in str(error.value)
    assert len(seen) == 1


def test_transport_blocks_other_origin_before_sending_credentials():
    api, _, seen = backend()
    with pytest.raises(SafetyBlocked):
        api._http.request("GET", "https://other.example/control/rewrite/list")
    assert seen == []


def test_transport_rejects_late_response_and_stops_after_request_budget():
    clock = [0.0]
    seen = []

    def handler(request):
        seen.append(request)
        clock[0] += 11
        return httpx.Response(200, json=[])

    client = ApiClient(
        "https://api.example",
        "basic",
        DnsCredentials(username="user", password="pass"),
        clock=lambda: clock[0],
        max_calls=1,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(DnsApiError):
        client.request("GET", "/control/rewrite/list")
    with pytest.raises(SafetyBlocked):
        client.request("GET", "/control/rewrite/list")
    assert len(seen) == 1
