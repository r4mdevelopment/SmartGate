import httpx
import pytest

from smartgate.dns.technitium import TechnitiumProvider
from smartgate.dns.transport import ApiClient
from smartgate.errors import DnsApiError, RecoveryRequired, SafetyBlocked
from smartgate.models import DesiredRewrite, DnsCredentials


def backend(records=None, case=None, allow=True):
    entries = list(records or [])
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["authorization"] == "Bearer sensitive-token"
        assert "sensitive-token" not in str(request.url) and "token" not in request.url.params
        assert "sensitive-token" not in request.content.decode()
        params = request.url.params
        assert params["zone"] == "isolated.example"
        if case == "error":
            return httpx.Response(200, json={"status": "error", "errorMessage": "sensitive-token"})
        if request.url.path.endswith("/get"):
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "response": {
                        "zone": {
                            "name": "other.example" if case == "wrong_zone" else "isolated.example",
                            "type": "Primary",
                            "disabled": False,
                            "internal": False,
                        },
                        "records": entries,
                    },
                },
            )
        assert request.method == "POST"
        assert request.url.path.startswith("/api/zones/records/")
        assert params["type"] == "A"
        if request.url.path.endswith("/add"):
            assert params["overwrite"] == "false" and params["ptr"] == "false"
            entries.append(
                {
                    "name": params["domain"],
                    "type": "A",
                    "ttl": 60,
                    "disabled": False,
                    "rData": {"ipAddress": params["ipAddress"]},
                }
            )
        elif request.url.path.endswith("/update"):
            row = next(
                r
                for r in entries
                if r["name"] == params["domain"]
                and r["rData"].get("ipAddress") == params["ipAddress"]
            )
            row["rData"]["ipAddress"] = params["newIpAddress"]
        elif request.url.path.endswith("/delete"):
            entries[:] = [
                r
                for r in entries
                if not (
                    r["name"] == params["domain"]
                    and r["rData"].get("ipAddress") == params["ipAddress"]
                )
            ]
        if case == "lost_mutation":
            raise httpx.ReadError("sensitive-token")
        return httpx.Response(200, json={"status": "ok"})

    http = ApiClient(
        "https://api.example",
        "bearer",
        DnsCredentials(api_key="sensitive-token"),
        transport=httpx.MockTransport(handler),
    )
    return TechnitiumProvider(http, "isolated.example", allow_reference_writes=allow), entries, seen


def test_scoped_reference_crud_never_creates_zones_or_sends_token_in_url():
    api, entries, seen = backend()
    old = api.create(DesiredRewrite("probe.isolated.example", "8.8.8.8"))
    new = api.replace(old, DesiredRewrite("probe.isolated.example", "1.1.1.1"))
    assert new.content == "1.1.1.1"
    api.delete(new)
    assert entries == []
    assert all("/zones/records/" in r.url.path for r in seen)


def test_non_a_records_are_visible_instead_of_dropped():
    api, _, _ = backend(
        [
            {
                "name": "isolated.example",
                "type": "SOA",
                "ttl": 60,
                "disabled": False,
                "rData": {"primaryNameServer": "ns.example"},
            },
            {
                "name": "v6.isolated.example",
                "type": "AAAA",
                "ttl": 60,
                "disabled": False,
                "rData": {"ipAddress": "2606:4700::1111"},
            },
        ]
    )
    rows = api.list_records()
    assert len(rows) == 2
    assert any(r.content == "2606:4700::1111" for r in rows)
    assert any("SOA" in r.content for r in rows)


@pytest.mark.parametrize("case", ["error", "wrong_zone"])
def test_error_or_wrong_zone_cannot_be_accepted(case):
    api, _, _ = backend(case=case)
    with pytest.raises(DnsApiError) as error:
        api.list_records()
    assert "sensitive-token" not in str(error.value)


def test_outside_scope_is_rejected_before_any_request():
    api, _, seen = backend()
    with pytest.raises(SafetyBlocked):
        api.create(DesiredRewrite("foreign.example", "8.8.8.8"))
    assert seen == []


def test_marker_does_not_authorize_runtime_writes():
    api, _, seen = backend(allow=False)
    with pytest.raises(SafetyBlocked):
        api.create(DesiredRewrite("probe.isolated.example", "8.8.8.8"))
    assert seen == []


def test_lost_write_response_requires_recovery_without_retry():
    api, _, seen = backend(case="lost_mutation")
    with pytest.raises(RecoveryRequired):
        api.create(DesiredRewrite("probe.isolated.example", "8.8.8.8"))
    assert sum(r.method == "POST" for r in seen) == 1


def test_duplicate_value_record_is_ambiguous_even_with_same_marker():
    row = {
        "name": "probe.isolated.example",
        "type": "A",
        "ttl": 60,
        "disabled": False,
        "comments": "smartgate",
        "rData": {"ipAddress": "8.8.8.8"},
    }
    api, _, _ = backend([row, row])
    with pytest.raises(DnsApiError):
        api.list_records()
