import httpx
import pytest

from smartgate.errors import DnsApiError, RecoveryRequired, SafetyBlocked
from smartgate.http import HttpClient


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def client(handler, **options):
    clock = Clock()
    return HttpClient(
        "unit-test-key",
        transport=httpx.MockTransport(handler),
        clock=clock.now,
        sleep=clock.sleep,
        jitter=lambda: 0.0,
        **options,
    ), clock


def test_errors_envelope_is_rejected_even_with_http_200():
    api, _ = client(lambda request: httpx.Response(200, json={"errors": [{"detail": "secret"}]}))
    with pytest.raises(DnsApiError) as error:
        api.request("GET", "/profiles/abcdef/rewrites")
    assert "secret" not in str(error.value)


def test_api_key_is_only_sent_to_fixed_nextdns_origin():
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["X-Api-Key"]))
        return httpx.Response(200, json={"data": []})

    api, _ = client(handler)
    assert api.request("GET", "/profiles/abcdef/rewrites") == {"data": []}
    assert seen == [("https://api.nextdns.io/profiles/abcdef/rewrites", "unit-test-key")]
    for path in ["https://other.example/x", "//other.example/x", "/x/../y"]:
        with pytest.raises(SafetyBlocked):
            api.request("GET", path)
    assert len(seen) == 1


@pytest.mark.parametrize("status", [401, 403, 400])
def test_auth_and_invalid_request_errors_are_not_retried(status):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(status, json={"errors": [{"detail": "unit-test-key"}]})

    api, _ = client(handler)
    with pytest.raises(DnsApiError) as error:
        api.request("GET", "/profiles/abcdef/rewrites")
    assert calls == ["GET"]
    assert "unit-test-key" not in str(error.value)


def test_read_timeout_retries_are_bounded():
    calls = []

    def handler(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("unit-test-key", request=request)

    api, _ = client(handler)
    with pytest.raises(DnsApiError) as error:
        api.request("GET", "/profiles/abcdef/rewrites")
    assert calls == ["GET", "GET", "GET"]
    assert "unit-test-key" not in str(error.value)


@pytest.mark.parametrize("method", ["POST", "DELETE", "PATCH"])
def test_unknown_mutation_result_is_not_blindly_retried(method):
    calls = []

    def handler(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("unit-test-key", request=request)

    api, _ = client(handler)
    with pytest.raises(RecoveryRequired):
        api.request(method, "/profiles/abcdef/rewrites")
    assert calls == [method]


def test_server_error_after_post_requires_reconciliation():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(503)

    api, _ = client(handler)
    with pytest.raises(RecoveryRequired):
        api.request(
            "POST", "/profiles/abcdef/rewrites", {"name": "example.com", "content": "8.8.8.8"}
        )
    assert calls == ["POST"]


def test_api_calls_obey_spacing_and_budget():
    times = []
    clock = Clock()

    def handler(request):
        times.append(clock.value)
        return httpx.Response(200, json={"data": []})

    api = HttpClient(
        "key",
        transport=httpx.MockTransport(handler),
        clock=clock.now,
        sleep=clock.sleep,
        jitter=lambda: 0.0,
        max_calls=3,
    )
    for _ in range(3):
        api.request("GET", "/profiles/abcdef/rewrites")
    with pytest.raises(SafetyBlocked):
        api.request("GET", "/profiles/abcdef/rewrites")
    assert times == [0.0, 2.0, 4.0]


def test_retry_after_larger_than_run_policy_stops():
    api, _ = client(lambda request: httpx.Response(429, headers={"Retry-After": "61"}))
    with pytest.raises(SafetyBlocked):
        api.request("GET", "/profiles/abcdef/rewrites")


def test_deadline_prevents_request_after_budget_expires():
    calls = []
    api, clock = client(
        lambda request: calls.append(request.method) or httpx.Response(200, json={"data": []}),
        duration_s=1,
    )
    api.request("GET", "/profiles/abcdef/rewrites")
    with pytest.raises(SafetyBlocked):
        api.request("GET", "/profiles/abcdef/rewrites")
    assert calls == ["GET"]
    assert clock.value == 0


def test_pagination_accumulates_all_records_and_rejects_cursor_loop():
    calls = []

    def handler(request):
        calls.append(request.url.params.get("cursor"))
        if len(calls) == 1:
            return httpx.Response(
                200, json={"data": [{"id": "one"}], "meta": {"pagination": {"cursor": "next"}}}
            )
        return httpx.Response(
            200, json={"data": [{"id": "two"}], "meta": {"pagination": {"cursor": None}}}
        )

    api, _ = client(handler)
    assert api.list_pages("/profiles/abcdef/rewrites") == [{"id": "one"}, {"id": "two"}]
    assert calls == [None, "next"]
    looping, _ = client(
        lambda request: httpx.Response(
            200, json={"data": [], "meta": {"pagination": {"cursor": "repeated"}}}
        )
    )
    with pytest.raises(DnsApiError):
        looping.list_pages("/profiles/abcdef/rewrites")


@pytest.mark.parametrize("payload", [None, [], {"data": [], "meta": {"pagination": []}}])
def test_malformed_api_envelopes_are_rejected(payload):
    api, _ = client(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(DnsApiError):
        if isinstance(payload, dict):
            api.list_pages("/profiles/abcdef/rewrites")
        else:
            api.request("GET", "/profiles/abcdef/rewrites")


def test_delete_204_is_success_without_a_json_envelope():
    api, _ = client(lambda request: httpx.Response(204))
    assert api.request("DELETE", "/profiles/abcdef/rewrites/record") == {}


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_empty_204_is_not_accepted_for_reads_or_creates(method):
    api, _ = client(lambda request: httpx.Response(204))
    expected = DnsApiError if method == "GET" else RecoveryRequired
    with pytest.raises(expected):
        api.request(method, "/profiles/abcdef/rewrites")
