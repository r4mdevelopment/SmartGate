from datetime import UTC, datetime

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import httpx
import pytest

from smartgate.dns.verify import DnsVerifier
from smartgate.errors import SafetyBlocked
from smartgate.models import DesiredRewrite


def verifier(case="valid"):
    clock = [0.0]
    seen = []

    def handler(request):
        assert str(request.url) == "https://resolver.example/dns-query"
        assert "x-api-key" not in request.headers
        seen.append(request)
        question = dns.message.from_wire(request.content)
        answer = dns.message.make_response(question)
        qtype = dns.rdatatype.to_text(question.question[0].rdtype)
        if qtype == "A":
            answer.answer.append(
                dns.rrset.from_text(
                    "example.com.", 60, "IN", "A", "1.1.1.1" if case == "wrong_a" else "8.8.8.8"
                )
            )
        elif qtype == "AAAA" and case == "bypass_aaaa":
            answer.answer.append(
                dns.rrset.from_text("example.com.", 60, "IN", "AAAA", "2606:4700::1111")
            )
        elif qtype in {"HTTPS", "SVCB"} and case == "bypass_https":
            answer.answer.append(
                dns.rrset.from_text("example.com.", 60, "IN", qtype, "1 . alpn=h2")
            )
        if case == "wrong_id":
            answer.id = (answer.id + 1) % 65536
        if case == "servfail":
            answer.set_rcode(dns.rcode.SERVFAIL)
        if case == "truncated":
            answer.flags |= dns.flags.TC
        if case == "unrelated":
            for rrset in answer.answer:
                rrset.name = dns.name.from_text("other.example.")
        return httpx.Response(
            200, content=answer.to_wire(), headers={"Content-Type": "application/dns-message"}
        )

    instance = DnsVerifier(
        "https://resolver.example/dns-query",
        transport=httpx.MockTransport(handler),
        clock=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        now=lambda: datetime(2026, 10, 7, tzinfo=UTC),
    )
    return instance, clock, seen


def test_verified_profile_answers_include_all_bypass_checks():
    dns, clock, seen = verifier()
    checks = dns.verify((DesiredRewrite("example.com", "8.8.8.8"),))
    assert len(checks) == 4 and all(c.ok for c in checks)
    assert {c.qtype for c in checks} == {"A", "AAAA", "HTTPS", "SVCB"}
    assert checks[0].answers == ("8.8.8.8",)
    assert len(seen) == 4 and clock[0] == 0


@pytest.mark.parametrize(
    "case",
    ["wrong_a", "bypass_aaaa", "bypass_https", "wrong_id", "servfail", "truncated", "unrelated"],
)
def test_mismatch_or_invalid_dns_never_passes(case):
    dns, clock, seen = verifier(case)
    checks = dns.verify((DesiredRewrite("example.com", "8.8.8.8"),))
    assert not all(c.ok for c in checks)
    assert clock[0] == 60
    assert all("key" not in c.reason for c in checks)


def test_propagation_waits_until_actual_selected_ip():
    instance, clock, seen = verifier("wrong_a")
    original = instance._client._transport.handler

    def handler(request):
        if clock[0] >= 5:
            q = dns.message.from_wire(request.content)
            a = dns.message.make_response(q)
            if q.question[0].rdtype == dns.rdatatype.A:
                a.answer.append(dns.rrset.from_text("example.com.", 60, "IN", "A", "8.8.8.8"))
            return httpx.Response(
                200, content=a.to_wire(), headers={"Content-Type": "application/dns-message"}
            )
        return original(request)

    instance._client._transport = httpx.MockTransport(handler)
    assert all(c.ok for c in instance.verify((DesiredRewrite("example.com", "8.8.8.8"),)))
    assert clock[0] == 5


def test_invalid_profile_or_duplicate_domain_rejected_without_dns_queries():
    dns, clock, seen = verifier()
    record = DesiredRewrite("example.com", "8.8.8.8")
    with pytest.raises(SafetyBlocked):
        dns.verify((record, record))
    assert seen == []


def test_observation_deadline_stops_new_questions_and_rejects_late_answers():
    clock = [0.0]
    calls = []

    def handler(request):
        calls.append(request)
        question = dns.message.from_wire(request.content)
        answer = dns.message.make_response(question)
        answer.answer.append(dns.rrset.from_text("example.com.", 60, "IN", "A", "8.8.8.8"))
        clock[0] += 61
        return httpx.Response(
            200, content=answer.to_wire(), headers={"Content-Type": "application/dns-message"}
        )

    instance = DnsVerifier(
        "https://resolver.example/dns-query",
        transport=httpx.MockTransport(handler),
        clock=lambda: clock[0],
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
    )
    checks = instance.verify((DesiredRewrite("example.com", "8.8.8.8"),))
    assert len(calls) == 1
    assert len(checks) == 4 and not any(c.ok for c in checks)


@pytest.mark.parametrize(
    "case",
    [
        "http_error",
        "wrong_content_type",
        "malformed",
        "oversize",
        "timeout",
        "wrong_question",
        "cname",
    ],
)
def test_query_failures_are_safe_and_never_leak_transport_text(case):
    def handler(request):
        if case == "timeout":
            raise httpx.ReadTimeout("unit-secret")
        if case == "http_error":
            return httpx.Response(403, content=b"unit-secret")
        if case == "wrong_content_type":
            return httpx.Response(
                200, content=b"unit-secret", headers={"Content-Type": "text/html"}
            )
        if case in {"malformed", "oversize"}:
            return httpx.Response(
                200,
                content=b"x" * (65536 if case == "oversize" else 4),
                headers={"Content-Type": "application/dns-message"},
            )
        question = dns.message.from_wire(request.content)
        answer = dns.message.make_response(question)
        if case == "wrong_question":
            answer.question = dns.message.make_query("other.example", "AAAA").question
        else:
            answer.answer.append(
                dns.rrset.from_text("example.com.", 60, "IN", "CNAME", "other.example.")
            )
        return httpx.Response(
            200, content=answer.to_wire(), headers={"Content-Type": "application/dns-message"}
        )

    from smartgate.errors import DnsApiError

    instance = DnsVerifier(
        "https://resolver.example/dns-query", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DnsApiError) as error:
        instance.query("example.com", "A")
    assert "unit-secret" not in str(error.value)


def test_query_request_budget_is_bounded():
    instance, clock, seen = verifier()
    instance._max_calls = 1
    instance.query("example.com", "A")
    with pytest.raises(SafetyBlocked):
        instance.query("example.com", "A")
    assert len(seen) == 1


def test_production_live_fixture_matches_profile_dns_contract():
    import json
    from pathlib import Path

    fixture = json.loads(Path("tests/fixtures/nextdns/production-roundtrip.json").read_text())
    observed = {
        item["qtype"]: item for item in fixture["dns"] if item["domain"] == "cloudflare.com"
    }
    assert fixture["cleanup_verified"]

    def handler(request):
        question = dns.message.from_wire(request.content)
        qtype = dns.rdatatype.to_text(question.question[0].rdtype)
        answer = dns.message.make_response(question)
        values = observed[qtype]["answers"]
        if values:
            answer.answer.append(dns.rrset.from_text("cloudflare.com.", 60, "IN", qtype, *values))
        return httpx.Response(
            200, content=answer.to_wire(), headers={"Content-Type": "application/dns-message"}
        )

    instance = DnsVerifier(
        "https://resolver.example/dns-query", transport=httpx.MockTransport(handler)
    )
    checks = instance.verify((DesiredRewrite("cloudflare.com", "8.8.8.8"),))
    assert len(checks) == 4 and all(check.ok for check in checks)


def test_doh_redirect_does_not_send_request_to_other_origin():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example/query"})

    from smartgate.errors import DnsApiError

    instance = DnsVerifier(
        "https://resolver.example/dns-query", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(DnsApiError):
        instance.query("example.com", "A")
    assert len(seen) == 1
    assert "authorization" not in seen[0].headers
    assert "x-api-key" not in seen[0].headers


def test_slow_drip_dns_body_stops_before_consuming_the_whole_response():
    from smartgate.errors import DnsApiError

    elapsed, consumed, closed = [0.0], [0], []

    class Drip(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(45):
                elapsed[0] += 3
                consumed[0] += 1
                yield b"x"

        def close(self):
            closed.append(True)

    dns = DnsVerifier(
        "https://resolver.example/dns-query",
        clock=lambda: elapsed[0],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"Content-Type": "application/dns-message"}, stream=Drip()
            )
        ),
    )
    try:
        with pytest.raises(DnsApiError):
            dns.query("example.com", "A", timeout_s=4)
        assert consumed[0] <= 2 and elapsed[0] <= 6 and closed
    finally:
        dns.close()


def test_dns_header_arrival_after_deadline_does_not_read_body():
    from smartgate.errors import DnsApiError

    elapsed, consumed = [0.0], []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            consumed.append(True)
            yield b"x"

    def response(request):
        elapsed[0] = 5
        return httpx.Response(
            200, headers={"Content-Type": "application/dns-message"}, stream=Body()
        )

    dns = DnsVerifier(
        "https://resolver.example/dns-query",
        clock=lambda: elapsed[0],
        transport=httpx.MockTransport(response),
    )
    try:
        with pytest.raises(DnsApiError):
            dns.query("example.com", "A", timeout_s=4)
        assert not consumed
    finally:
        dns.close()
